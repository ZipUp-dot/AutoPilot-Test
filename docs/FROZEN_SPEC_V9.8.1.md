# AutoPilot-Test V9.8.1

## 架构审计结果 + 原方案漏洞总账 + Frozen Implementation Master Spec

---

# 一、项目基线

AutoPilot-Test 的原始立项定位，是一个轻量级、开箱即用的 AI 驱动 Web UI 自动化测试平台，核心链路为：

```text
环境配置
  ↓
元素抓取
  ↓
用例导入
  ↓
AI 精准生成
  ↓
可视化执行
  ↓
智能自愈
  ↓
报告输出
```

立项方案同时把“环境感知生成”“可视化执行”“自愈”和“轻量化部署”作为核心设计方向。原方案采用 Vue3 + FastAPI + Playwright + LLM + SQLite/MySQL + Docker Compose，并明确以 MVP 闭环为第一阶段目标。 

因此，本次架构治理始终遵循两个原则：

```text
原则 1：解决真实一致性问题，而不是为了复杂而复杂

原则 2：不引入 Redis / Celery / Kafka / 分布式 Worker 等额外基础设施，
       除非项目需求发生变化
```

最终目标不是把 AutoPilot 做成“大而全”的任务平台，而是让现有轻量架构在：

```text
异步执行
AI 生成
代码版本
自愈
停止
恢复
状态统计
报告
前后端数据
数据库迁移
```

这些复杂场景下仍然保持**单一事实源、可追溯、可恢复、可验证**。

---

# 二、原方案漏洞总账

下面是前序审查中发现的问题，已经去重整理。

## 1. Batch AI 生成存在双入口

原设计中存在两套 Batch 实现：

```text
AIService.generate_batch()
Router 内部 BatchJob
```

两套逻辑可能产生：

```text
不同的异常处理
不同的重试
不同的统计
不同的任务状态
```

### 修正

统一为：

```text
BatchGenerateService
        ↓
BatchJob
        ↓
Worker
        ↓
AIService.generate_single()
```

所有入口共享同一套 Batch 生命周期。

---

## 2. CodeValidator 不统一

原设计存在：

```text
AIService._validate_syntax()
PlaywrightService CodeValidator
运行时安全检查
```

导致同一份代码可能出现：

```text
生成时认为合法
执行时认为非法
```

### 修正

建立统一：

```text
CodeValidator
```

同时用于：

```text
AI 生成后
Admission
执行前
Heal Candidate
```

---

## 3. AI 限流语义不清

原方案只描述了“限制 AI 调用次数”，没有明确：

```text
并发槽位到底覆盖整个重试过程？
还是只覆盖单次 HTTP attempt？
```

### 修正

采用：

```text
Rate Quota
    ↓
Acquire Slot
    ↓
单次 HTTP Attempt
    ↓
Release Slot
```

并明确：

```text
sleep/backoff 不占用 slot
一次 retry = 新的 HTTP attempt
```

---

## 4. Batch 并发模型存在竞态

原方案直接考虑并发，却没有先定义：

```text
谁领取任务
谁确认任务状态
两个 Worker 会不会拿到同一个 Case
```

### 修正

采用两个 Pull Worker：

```text
Worker A ─┐
          ├─ Claim Pending Case
Worker B ─┘
```

Claim 必须是原子操作。

---

## 5. Batch 失败没有阻断 Pipeline

原方案存在：

```text
AI 生成失败
    ↓
继续执行
```

这会造成：

```text
生成失败
    ↓
Execution 已创建
    ↓
执行阶段才发现没有有效代码
```

### 修正

Full Pipeline 使用：

```text
All-or-None Admission
```

即：

```text
请求 20 条
↓
必须 20 条都具有 admissible code
↓
才能创建 Execution
```

否则：

```text
不创建 Execution
```

并按 Case 返回失败原因。

---

## 6. “最新代码”语义存在危险

原方案曾经：

```text
取 latest GeneratedCode
```

但可能出现：

```text
最新记录 = invalid
旧记录 = valid
```

如果系统偷偷回退：

```text
最新 invalid
    ↓
偷偷使用旧 valid
```

用户看到的“当前代码”就和实际执行代码不一致。

### 修正

采用：

```text
Newest authoritative GeneratedCode
```

规则：

```text
最新记录必须满足：

is_valid = 1
source_steps_hash = current TestCase steps_hash
并符合当前执行策略
```

否则：

```text
无可用代码
不允许偷偷 fallback
```

排序固定为：

```sql
ORDER BY created_at DESC, id DESC
```

---

## 7. 生成代码没有与用例定义建立来源绑定

这是后期才发现的真实缺口。

仅有：

```text
Execution.steps_hash
```

只能防止：

```text
Execution 创建以后
TestCase 被修改
```

但是无法防止：

```text
H1 → 生成代码 C1

TestCase → H2

新的 Execution
↓
错误复用 C1
```

### 修正

GeneratedCode 增加：

```text
source_steps_hash
```

形成：

```text
TestCase
   ↓
StepCanonicalizer
   ↓
steps_hash
   ├───────────────┐
   ↓               ↓
GeneratedCode    Manifest
source_steps_hash steps_hash
```

最终必须满足：

```text
GeneratedCode.source_steps_hash
==
ExecutionCase.steps_hash
```

---

## 8. Step Hash 没有统一算法

如果不同模块各写一套 hash：

```text
生成模块 hash A
Admission hash B
Manifest hash C
```

那么 hash 就失去意义。

### 修正

只有一个：

```text
StepCanonicalizer
```

流程固定：

```text
标准化 Step
↓
Canonical JSON
↓
SHA-256
```

所有模块统一调用。

---

## 9. Execution 创建后仍然重新读取 TestCase

原实现中曾存在：

```text
Execution 创建
↓
运行时重新读取 TestCase
↓
重新构建 ExecutionStep
```

这会导致：

```text
Execution 创建时是 H1
运行时 TestCase 已变成 H2
最终执行了 H2
```

### 修正

Execution 创建时一次性冻结：

```text
Execution
ExecutionCase
ExecutionStep
Manifest
```

运行阶段不得重新读取当前 TestCase 作为执行定义。

---

## 10. ExecutionStep 定义和执行结果没有分层

原方案容易把：

```text
Step Definition
Step Runtime Outcome
```

混在一起。

### 修正

ExecutionStep 中：

### 不可变定义字段

```text
step_index
action
target_selector
input_value
assertion
```

### 可变结果字段

```text
status
screenshot_before
screenshot_after
log_output
error_message
exception_type
duration_ms
skip_reason
```

---

## 11. Case 状态曾经使用“任意失败即失败”

原实现：

```text
只要某个 Step failed
Case = failed
```

在 Self-Healing 场景下会造成：

```text
第一次失败
↓
自愈成功
↓
后续 Step success
↓
历史 failed 仍然污染最终结果
```

### 修正

建立：

```text
CaseStateResolver
```

只解释当前 Execution 内的最终 Step 状态。

---

## 12. CaseStateResolver 与 Execution.status 曾有潜在循环依赖

错误结构：

```text
Case Result
 ↓
Execution Status
 ↓
Case Result
```

最终可能产生循环。

### 修正

：

```text
Execution Lifecycle
        ↓
Execution.status
```

和：

```text
ExecutionStep[]
        ↓
CaseStateResolver
        ↓
CaseResult
```

二者相互独立。

Resolver 不读取 Execution.status 决定 Case 状态。

---

## 13. Case 与 Execution 的失败语义混淆

原方案容易把：

```text
一个业务用例失败
```

写成：

```text
Execution.failed
```

这样执行 20 个用例，其中 3 个业务失败，就会变成“整个 Execution failed”。

### 修正

：

```text
Case failed
=
业务测试失败

Execution failed
=
执行基础设施/数据完整性/Worker 等级故障
```

所以正常情况下：

```text
3 failed Cases
+
17 success Cases
=
Execution completed
```

---

## 14. success + skipped 的语义错误

曾经存在：

```text
success + skipped
↓
success
```

这是不成立的。

例如：

```text
Step 1 success
Step 2 success
Step 3 skipped
```

不能声称 Case 完整成功。

### 修正

：

```text
success + skipped
↓
failed
reason = incomplete_execution
```

---

## 15. pending + success/skipped 没有完整语义

例如：

```text
success
pending
```

这意味着执行数据不完整。

### 修正

：

```text
pending + success/skipped
↓
unknown
```

unknown：

```text
不是业务失败
但是属于数据完整性异常
```

---

## 16. unknown 与业务 failed 没有区分

如果 unknown 被直接统计成普通 failed：

```text
业务失败
```

和：

```text
系统数据坏了
```

就被混为一谈。

### 修正

：

```text
unknown
↓
integrity_anomaly
↓
Execution.failed
```

---

## 17. ExecutionStep 重试历史与 Execution 混在一起

原设计一度把 Heal 直接绑定到某个 Step，而没有明确：

```text
一次重新执行
到底是不是新的 Execution？
```

### 修正

：

```text
Rerun
=
新的 Execution

Heal
=
当前 Execution 内部的一次修复轮次
```

即：

```text
Execution
  ├─ Case
  │   ├─ original Step outcome
  │   └─ Heal Round
  │
  └─ ...
```

---

## 18. Heal 是 Step 级还是 Case 级的问题

原设计倾向：

```text
某个 Step 失败
↓
只修这个 Step
```

但 Playwright 实际执行上下文是整条 Case。

### 修正

：

```text
Heal Unit = Case
```

失败 Root Step：

```text
当前 Case 内
exception/error
对应的最小 step_index
```

作为根失败。

---

## 19. Heal 次数保护不足

单独存在：

```text
全局缓存
```

不足以阻止：

```text
同一 Execution
同一 Case
重复 Heal
```

### 修正

双重防线：

```text
第一层：
Execution + Case + Round
DB UNIQUE

第二层：
(case_id, error_type)
跨 Execution cost-control cache
```

二者目的不同。

---

## 20. Failed Heal Candidate 曾经错误写入 GeneratedCode

原方案有：

```text
AI 生成 candidate
↓
立即保存 GeneratedCode
↓
再执行验证
```

这样数据库里会出现：

```text
“看起来有很多代码版本”
```

但其中大量其实不可用。

### 修正

：

```text
Candidate
↓
存在 HealRecord.attempts
↓
执行
↓
成功
↓
才写 GeneratedCode
```

失败 Candidate 不进入 GeneratedCode。

---

## 21. Heal 原始代码来源曾经使用 latest code

这是典型的版本污染。

### 错误

```text
Heal
↓
重新查询 latest GeneratedCode
```

### 修正

：

```text
Manifest.original_code_id
        ↓
RuntimeState.active_code_id
        ↓
ExecutionCodeResolver
```

Execution / Heal 全程禁止：

```text
latest GeneratedCode
```

作为实际执行来源。

---

## 22. Heal 之后没有明确“当前有效代码指针”

如果成功产生新代码：

```text
新的 GeneratedCode
```

但没有定义：

```text
这次 Execution 后续继续执行谁？
```

就会产生版本漂移。

### 修正

：

```json
runtime_state_json
{
  "case_123": {
    "active_code_id": 456
  }
}
```

Heal 成功：

```text
新 GeneratedCode
↓
更新 active_code_id
↓
后续 ExecutionCodeResolver 使用新版本
```

---

## 23. Execution Manifest 曾缺少足够快照

只保存 Case ID 是不够的。

Case Name / Priority / Environment 在未来都可能变化。

### 修正

Manifest 冻结：

```text
project_name_snapshot
batch_name_snapshot
target_url
test_path
browser_type
execution_mode

case_name_snapshot
priority_snapshot

step_count
steps_hash
expected_steps_snapshot

original_code_id
```

---

## 24. Execution Environment 曾有多个事实源

曾经可能出现：

```text
crawl 用 target_url
AI prompt 用 target_url
execution 用 target_url + 另外逻辑
heal 又自己拼一次
```

### 修正

统一：

```text
build_target_url()
```

所有模块：

```text
Crawl
Precheck
AI Prompt
Execution
Heal
```

使用相同结果。

---

## 25. browser_type 是配置字段，但执行引擎没有真正使用

例如：

```text
browser_type = firefox
```

实际还是：

```python
chromium.launch()
```

这属于配置语义与运行语义不一致。

### 修正

Manifest 的 browser_type 必须实际驱动 Playwright Launch。

---

## 26. Stop 曾经只是进程内 flag

原实现：

```text
_stop_flags
```

导致：

```text
进程重启
↓
Stop 意图消失
```

### 修正

持久化：

```text
stop_requested_at
```

Recovery 时优先级：

```text
stop intent
>
interrupted
```

---

## 27. Stop API 与前端语义不一致

原前端：

```text
点击停止
↓
立即显示“已停止”
↓
停止 polling
```

但后端真实语义应该是：

```text
收到停止请求
↓
当前 Case 收尾
↓
边界停止
↓
Execution.status = stopped
```

### 修正

前端：

```text
停止请求成功
↓
显示“正在停止”
↓
继续 polling
↓
真正 status=stopped
↓
才显示“已停止”
```

---

## 28. Stop 与 Healing 存在优先级问题

否则可能：

```text
用户已经点 Stop
↓
后台又开始新的 Heal
```

### 修正

：

```text
Stop Intent > Healing Decision
```

Stop 后不允许启动新的 Heal Round。

---

## 29. Stop 后真实业务失败不应该全部排除

例如：

```text
Case A：
自然失败

然后用户点 Stop
```

如果简单地：

```text
Execution.stopped
↓
全部排除 85% KPI
```

就会把 Case A 错误排除。

### 修正

使用：

```text
Case.terminal_reason
```

而不是：

```text
Execution.status
```

来决定 KPI eligibility。

---

## 30. Recovery 曾经只按 updated_at 判断孤儿任务

`updated_at` 不等于 worker heartbeat。

### 修正

：

```text
worker_id
heartbeat_at
lease
```

作为 Recovery 基础。

---

## 31. Worker 崩溃没有明确 Supervisor

单纯：

```text
ThreadPool
```

无法很好区分：

```text
Worker 正常完成
Worker 异常退出
整个 Batch 已经不能继续
```

### 修正

：

```text
BatchJob
  ↓
Supervisor
  ├─ Worker A
  └─ Worker B
```

Worker fatal：

```text
停止继续 claim 新任务
等待现有 Worker
关闭剩余 Pending
BatchJob = failed
```

---

## 32. Batch Circuit 的触发点曾不明确

如果在 AI attempt 层累计：

```text
failed
failed
failed
```

但是这些只是同一个 Case 的不同 retry，就可能提前熔断。

### 修正

按：

```text
Case terminal event
```

统计连续失败，而不是：

```text
AI attempt event
```

---

## 33. Mock 曾经与真实 AI 结果混在一起

没有元数据就无法回答：

```text
这个代码到底是真 AI 生成的？
还是本地 mock？
```

### 修正

GeneratedCode：

```text
is_mock
```

并把 Mock 作为一等状态。

---

## 34. Mock 与 skipped 混淆

Mock 代码实际上可能是可执行的。

### 修正

：

```text
Mock
=
BatchCase success

Skipped
=
没有执行
```

所以：

```text
Mock → green success
Skipped → skipped
```

---

## 35. 70% 指标曾被当成“真实业务准确率”

CodeValidator 通过：

```text
≠
业务执行成功
```

### 修正

改名为：

```text
首生成有效率
```

它是：

```text
Engineering Proxy
```

而不是严格意义上的：

```text
Business Accuracy
```

---

## 36. 70% 指标 Cohort 定义不足

什么 Case 应该进分母没有明确。

### 修正

只统计：

```text
实际发生真实 AI attempt
+
非 Mock
+
不是 worker-before-AI
+
不是 circuit-before-AI
+
没有 deadline 排除
```

没有 cohort：

```text
N/A
```

而不是：

```text
0%
```

---

## 37. 85% Final Success KPI 分母曾模糊

Execution stopped 并不意味着里面所有 Case 都应该排除。

### 修正

Case eligibility 看：

```text
terminal_reason
```

排除：

```text
user_stopped
interrupted
```

保留：

```text
normal_success
business_failure
execution_failed
integrity_anomaly
```

这样真实业务失败不会因“用户后来点了停止”而神秘消失。

---

## 38. Batch 计数器命名不清

`attempted_count` 很容易被误读成：

```text
AI attempt
Case processed
Execution attempted
```

### 修正

使用明确字段：

```text
requested_count
processed_count
ai_attempted_count
kpi_eligible_count
first_gen_valid_count
mock_count
```

---

## 39. Execution Counters 曾被当成事实源

例如：

```text
Execution.passed_cases
```

与：

```text
CaseResult.success count
```

可能不一致。

### 修正

：

```text
CaseResult / Resolver
=
事实

Execution counters
=
derived cache
```

缓存错了可以 rebuild。

---

## 40. Report 使用当前 TestCase 信息

历史 Execution 如果直接读取当前 TestCase：

```text
昨天叫 A
今天改成 B
```

报告会显示：

```text
B
```

### 修正

Report 使用：

```text
Manifest
+
Runtime
```

而不是当前 TestCase。

---

## 41. Report 对 stopped / failed / interrupted 处理不完整

原逻辑偏向：

```text
只有 completed 才报告
```

### 修正

：

```text
completed
→ full report

stopped
→ partial report

failed
→ diagnostic report if evidence exists

interrupted
→ diagnostic report
```

---

## 42. Report 可能重复生成

并发或重复点击都可能：

```text
生成两个报告
```

### 修正

：

```text
Execution + report generation type
UNIQUE
```

同时：

```text
claim
→ temp file
→ rename
```

避免半成品文件。

---

## 43. Execution 终态与 Case / Heal Round 没有统一收口

可能出现：

```text
Execution = completed
但某个 Case = running

或：

Execution = stopped
HealRound 仍然 open
```

### 修正

Execution terminal 前必须满足：

```text
所有 admitted Case 都 terminal
+
所有 HealRound 都 terminal
+
没有 unknown
```

---

## 44. Execution Seal 缺失

没有 Seal 就意味着：

```text
报告生成时
Execution 数据可能还在继续变化
```

### 修正

终态后：

```text
Execution Seal
```

之后：

```text
ExecutionCase
ExecutionStep
RuntimeState
HealRecord
```

都不再发生业务意义上的变更。

---

## 45. 数据库 Migration 存在双权威

原方案同时存在：

```text
schema.sql
自定义 _run_migrations
Alembic
```

会形成：

```text
Schema Evolution Authority > 1
```

### 修正

唯一权威：

```text
Alembic
```

Legacy DB：

```text
Legacy Bridge
↓
stamp baseline
↓
后续全部 Alembic upgrade
```

---

## 46. SQLite FK 默认容易被忽略

Schema 写了 FK：

```text
ON DELETE ...
```

并不意味着 SQLite 每个连接都真正执行约束。

### 修正

每条 SQLite Connection 都必须：

```sql
PRAGMA foreign_keys = ON;
```

---

## 47. Cascade Delete 与历史审计需求冲突

原设计大量：

```text
ON DELETE CASCADE
```

如果删除 Project：

```text
Execution
ExecutionStep
HealRecord
GeneratedCode
```

全部消失。

历史报告就无法追溯。

### 修正

历史对象使用：

```text
RESTRICT
```

不能因为删除上游业务对象把执行审计记录级联清掉。

---

## 48. SQLite Migration 不能只靠 ADD COLUMN

涉及：

```text
FK
UNIQUE
历史约束
表结构
```

时，SQLite 经常需要 table rebuild。

### 修正

Migration 必须：

```text
Preflight
↓
Table Rebuild if required
↓
Data Validation
↓
Constraint Validation
```

---

## 49. Migration 出错不能静默吞掉

原方案的：

```python
except:
    pass
```

会产生：

```text
Migration 看起来成功
实际 Schema 已经半残
```

### 修正

：

```text
失败必须显式失败
```

不能吞掉。

---

## 50. 前端统计口径曾与后端不一致

曾出现：

```text
Execution.passed_cases = 1
CaseResult success = 2
```

造成：

```text
顶部显示 1
列表显示 2
```

### 修正

前端统计卡必须来自：

```text
CaseResult
```

而不是一套又一套字段。

---

## 51. 前端状态展示契约没有完全冻结

后来补齐：

```text
Mock → success / green

failed → error_type tooltip

skipped → skip_reason

unknown → “执行数据异常”

Admission failure
→ 按 Case 显示失败原因
```

同时：

```text
Stop requested
→ 正在停止
→ 继续 polling
→ stopped 后才结束
```

---

# 三、对当前 Final Implementation Amendment 的独立再审

本次不是继续顺着前面的结论往下写，而是重新从“这个文档单独拿出去给别人实施，会不会产生冲突”的角度验收。

## 结论

### 结构性漏洞

**未发现。**

目前不存在新的：

```text
A → B
同时又规定
B → 非 A
```

这样的直接矛盾。

尤其以下几组目前已经形成闭环：

```text
TestCase
   ↓
StepCanonicalizer
   ↓
steps_hash
   ├─────────────┐
   ↓             ↓
GeneratedCode   Manifest
   ↓             ↓
source_hash     steps_hash
   └──────┬──────┘
          ↓
       Admission
```

以及：

```text
Execution
 ↓
ExecutionCase
 ↓
ExecutionStep[]
 ↓
CaseStateResolver
 ↓
CaseResult
 ↓
ExecutionStatsResolver
 ↓
Cache / Report / UI
```

以及：

```text
original_code_id
      ↓
runtime active_code_id
      ↓
ExecutionCodeResolver
      ↓
当前真正执行代码
```

和：

```text
Stop Intent
      ↓
Stop Boundary
      ↓
Case terminalization
      ↓
Execution.stopped
      ↓
Partial Report
```

这些关键闭环已经成立。

---

# 四、本轮唯一建议“写死”的实现级定义

这些不是新漏洞，也不需要新版本，只是把最终规格再钉死，避免以后开发人员有不同理解。

## D1. Mock Admission Predicate

最终建议统一表述为：

```text
Admissible Code =
    valid real code
    OR
    explicit mock code
```

但：

```text
Mock 必须带 is_mock = 1
```

并且所有 KPI 必须知道它是 Mock。

---

## D2. Circuit Scope

Circuit 的失败 streak 最好明确限定为：

```text
BatchJob 内部
```

而不是整个 Uvicorn Process 的所有 Batch 共用一条 streak。

否则：

```text
Batch A 连续失败
↓
熔断
↓
Batch B 刚启动
↓
也被影响
```

对于 MVP，更合理的是：

```text
BatchJob
 └─ CircuitState
```

如果未来需要跨 Batch 的 Provider 级熔断，再单独引入。

---

## D3. Stop 与“已在飞”的 Heal Attempt

应明确：

```text
Stop request
+
Heal AI request 已经发出
```

时：

```text
允许当前 HTTP attempt 按现有 timeout/deadline 收尾
但禁止开启新的 Heal attempt
且禁止开启新的 Heal Round / Case rerun
```

否则就会出现：

```text
用户已经停止
↓
AI response 恰好回来
↓
系统又开启第二轮修复
```

这种竞态。

---

## D4. KPI 0 Denominator

所有指标统一：

```text
eligible_count = 0
↓
N/A
```

不能显示：

```text
0%
```

因为：

```text
没有样本
≠
准确率 0
```

---

# 五、最终 Frozen Master Specification

以下作为真正的长期实现基线。

---

## §1 项目定位

轻量级 AI Web UI 自动化测试平台。

核心路径：

```text
Environment
→ Crawl
→ Import
→ Generate
→ Execute
→ Heal
→ Report
```

---

## §2 架构边界

MVP：

```text
Vue3
FastAPI
Playwright
SQLite
LLM
Docker Compose
```

不主动引入：

```text
Redis
Kafka
Celery
分布式消息队列
分布式 Worker
Embedding
复杂调度系统
```

---

## §3 核心对象

系统核心对象：

```text
Project
TestCase
GeneratedCode
BatchRecord
Execution
ExecutionCase
ExecutionStep
HealRecord
Report
```

---

## §4 TestCase

TestCase 是：

```text
当前用户定义
```

不是历史 Execution 的事实源。

因此：

```text
TestCase 可以变化
Execution 不跟着变化
```

---

## §5 GeneratedCode

GeneratedCode 是：

```text
代码版本历史
```

必须包含：

```text
code_content
is_valid
is_mock
is_healed
source_steps_hash
created_at
```

---

## §6 StepCanonicalizer

统一：

```text
normalized steps
→ canonical JSON
→ SHA256
```

所有生成/Admission/Manifest/验证都使用同一实现。

---

## §7 CodeValidator

唯一的代码验证器。

入口统一：

```text
Generation
Admission
Execution
Heal
```

---

## §8 Current Effective Code

查询：

```text
ORDER BY created_at DESC, id DESC
```

以最新记录为权威。

只有同时满足：

```text
source_steps_hash == current steps_hash
+
is_valid = 1
+
符合当前 execution policy
```

才可使用。

新设计下：

```text
legacy source_steps_hash = NULL
```

的历史代码不得直接参与新的执行 Admission。

---

## §9 Mock

Mock 是一等元数据：

```text
is_mock = true
```

UI：

```text
绿色 success
```

而不是：

```text
skipped
```

---

## §10 BatchJob

BatchJob 生命周期：

```text
created
→ running
→ completed
or failed
```

任务状态和 Execution 状态分离。

---

## §11 Batch Generation Admission

Batch 请求首先检查：

```text
requested cases
```

每个 Case 必须明确落在：

```text
success
validation_error
mock
...
```

不能静默少生成几条然后继续执行。

---

## §12 Worker

使用：

```text
2 Pull Workers
```

Worker：

```text
claim
→ generate
→ persist
→ terminalize BatchCase
```

---

## §13 AI Limiter

结构：

```text
quota
↓
slot
↓
HTTP attempt
↓
release
```

backoff 不占 slot。

---

## §14 AI Retry

Retry 是：

```text
新的 HTTP attempt
```

而不是新的 BatchCase。

必须记录：

```text
attempt number
error type
duration
provider response
```

---

## §15 Circuit Breaker

以：

```text
Case terminal event
```

作为 streak 单位。

默认：

```text
non-retryable failure
不自动 reset
```

Mock：

```text
不计入真实 AI streak
```

Circuit 默认限定在 BatchJob。

---

## §16 Deadline

每个 Batch / Case 都有明确 deadline。

超时：

```text
deadline_exceeded
```

表示：

```text
本次 Batch / Execution 没能在预算内完成
```

但一个本身有效的 GeneratedCode：

```text
仍然可以作为未来 Admission 的代码资产
```

---

## §17 First-generation KPI

名称：

```text
首生成有效率
```

不是：

```text
真正业务准确率
```

Eligibility：

```text
真实 AI attempt
+
非 Mock
+
未在 AI 前被 circuit/worker/deadline 拦截
```

分母：

```text
eligible_count
```

分子：

```text
第一份 AI 输出通过 CodeValidator
```

无样本：

```text
N/A
```

---

## §18 BatchRecord

建议记录：

```text
batch_id
requested_count
processed_count
ai_attempted_count
kpi_eligible_count
first_gen_valid_count
mock_count
status
timestamps
```

`attempted_count` 不再作为含义不明确的总字段。

---

## §19 ExecutionAdmissionService

所有 Execution 创建入口统一：

```text
ExecutionAdmissionService
```

不能存在：

```text
Router 自己 new Execution
Service 又 new Execution
Orchestrator 再 new Execution
```

多个事实源。

---

## §20 Admission 原子性

一次事务完成：

```text
Execution
+
ExecutionCase[]
+
ExecutionStep[]
```

成功 commit 后：

```text
才进入 queued
```

不能：

```text
Execution 已创建
但是 ExecutionCase 只有一半
```

---

## §21 Execution Manifest

Manifest 冻结：

```text
project
batch
environment
browser
mode
case name
priority
steps
step hash
original code id
```

以后报告都从这里读取。

---

## §22 ExecutionCase

ExecutionCase 是：

```text
一次 Execution 对某个 TestCase 的历史快照
```

关键字段：

```text
case_id
case_name_snapshot
priority_snapshot
expected_steps_snapshot
expected_step_count
steps_hash
original_code_id
```

并：

```text
UNIQUE(execution_id, case_id)
```

---

## §23 Runtime State

Runtime State 保存：

```text
case_id
active_code_id
```

例如：

```json
{
  "128": {
    "active_code_id": 932
  }
}
```

这是 Execution 内当前有效代码指针。

---

## §24 ExecutionCodeResolver

Execution 和 Heal 获取代码时：

```text
ExecutionCodeResolver
```

只能读取：

```text
Manifest original_code_id
or
Runtime active_code_id
```

禁止：

```text
直接 query latest GeneratedCode
```

---

## §25 ExecutionStep

ExecutionStep 必须提前创建。

：

```text
Admission
↓
all Steps materialized
```

而不是运行前重新从 TestCase 生成。

---

## §26 ExecutionStep Definition Immutable

不可修改：

```text
step_index
action
target
selector
input
assertion
```

可修改：

```text
status
screenshot
log
error
duration
skip_reason
```

---

## §27 CaseStateResolver

Case 状态只来自：

```text
ExecutionStep[]
```

Resolver 不读取：

```text
Execution.status
```

---

## §28 Case State Matrix

固定：

```text
any running
→ running

any failed
→ failed

all success
→ success

all skipped
→ skipped

success + skipped
→ failed(incomplete_execution)

all pending
→ pending

pending + success/skipped
→ unknown
```

未知 Step 状态：

```text
→ unknown
```

---

## §29 terminal_reason

terminal_reason 是：

```text
Case 为什么结束
```

而不是：

```text
Execution 最终是什么状态
```

允许：

```text
normal_success
business_failure
user_stopped
interrupted
execution_failed
integrity_anomaly
```

优先依据真正造成 Case terminalization 的事件。

---

## §30 Execution Lifecycle

Execution：

```text
queued
↓
running
↓
healing
↓
completed
```

也允许：

```text
stopped
failed
interrupted
```

其中：

```text
Case failed
≠
Execution failed
```

---

## §31 Stop Intent

Stop API：

```text
stop_requested_at = now
```

这是持久化事实。

不要只依赖：

```text
process-local flag
```

---

## §32 Stop Boundary

停止检查点：

```text
领取下一个 Case 前
当前 Case 开始前
下一个 Step 开始前
新 Heal Round 前
新 Heal Attempt 前
```

当前已经运行中的动作：

```text
按当前 timeout/deadline 收尾
```

但不得继续启动新的业务工作。

---

## §33 Frontend Stop Contract

用户点击：

```text
停止
```

UI：

```text
正在停止...
```

继续 polling。

只有：

```text
status == stopped
```

才变成：

```text
已停止
```

---

## §34 Recovery

启动 Recovery：

```text
heartbeat_at
+
worker_id
+
lease
```

判断 orphan。

如果：

```text
stop_requested_at != NULL
```

则：

```text
stopped
```

优先于：

```text
interrupted
```

否则：

```text
running/healing stale
→ interrupted
```

---

## §35 Heal Unit

Heal Unit：

```text
Case
```

不是单个 Step。

---

## §36 Root Failure Step

一个 Case 同时存在多个 failed Step 时：

```text
最小 step_index
```

作为 Root Failed Step。

原因：

```text
它更接近原始失败源
```

---

## §37 Heal Round

：

```text
Execution + Case + RoundNo
```

唯一：

```text
UNIQUE(execution_id, case_id, round_no)
```

默认：

```text
一轮
```

---

## §38 Heal Attempts

一轮最多：

```text
3 attempts
```

每个 candidate 必须：

```text
validate
→ execute
```

---

## §39 Failed Candidate Persistence

失败 Candidate：

```text
只进入 HealRecord.attempts
```

成功 Candidate：

```text
才创建 GeneratedCode
```

因此 GeneratedCode 永远只表示：

```text
曾经被认可为有效的代码版本
```

---

## §40 Heal Finalization

成功 Heal：

```text
新 GeneratedCode
+
HealRecord terminal
+
Runtime active_code_id
```

必须在一个数据库事务里完成。

如果提交失败：

```text
Heal Round 保持可重试 finalizing 状态
```

不能半完成。

---

## §41 Execution Seal

终态之前确认：

```text
所有 ExecutionCase terminal
+
所有 HealRound terminal
+
无 unknown
```

之后：

```text
Seal
```

任何后续报告都基于冻结数据。

---

## §42 Report Source

Report 数据来源：

```text
Manifest
+
ExecutionCase
+
ExecutionStep
+
Runtime active code
+
CaseStateResolver
```

不能：

```text
重新查询当前 TestCase
```

---

## §43 Report Idempotency

Report 生成：

```text
Execution + ReportType
```

必须具备唯一 claim。

文件：

```text
temp
↓
write
↓
rename
```

防止半成品。

---

## §44 Terminal Report Policy

：

```text
completed
→ Full Report

stopped
→ Partial Report

failed
→ Diagnostic Report

interrupted
→ Diagnostic Report
```

不能因为：

```text
不是 completed
```

就完全没有证据。

---

## §45 Frontend Result Contract

用例列表：

```text
success
→ 正常绿色

failed
→ 展示 error_type tooltip

skipped
→ 展示 skip_reason

unknown
→ 警示“执行数据异常”

mock
→ success 绿色
```

Admission 失败：

```text
逐 Case 展示失败原因
```

---

## §46 Final Success KPI

85% 指标统计 Case，而不是简单看：

```text
Execution.status
```

分母排除：

```text
user_stopped
interrupted
```

保留：

```text
normal_success
business_failure
execution_failed
integrity_anomaly
```

Mock 的 AI 生成指标：

```text
N/A
```

执行结果本身仍可以作为产品原始执行统计，但必须标记 Mock cohort，不能冒充真实 AI 生成能力。

---

## §47 Progress

Progress 分为：

```text
case_progress
step_progress
```

不要用一个百分比同时表达两个不同概念。

`started` 定义冻结为：

```text
任何 ExecutionStep 第一次进入 running
```

因此：

```text
Admission != Started
```

Pipeline Coverage：

```text
admitted / requested
```

Execution Start Coverage：

```text
started / requested
```

---

## §48 Environment Consistency

统一：

```text
build_target_url()
```

所有：

```text
crawl
precheck
generation
execution
heal
```

使用相同环境语义。

`browser_type` 必须真实驱动 Playwright。

---

## §49 Schema Evolution

唯一 Schema Evolution Authority：

```text
Alembic
```

旧数据库：

```text
No alembic_version
↓
Legacy Bridge
↓
stamp baseline
```

之后：

```text
Alembic upgrade
```

不允许：

```text
Alembic
+
_schema.sql
+
_run_migrations
```

同时负责版本演化。

---

## §50 Data Safety

FK：

```text
RESTRICT
```

SQLite：

```sql
PRAGMA foreign_keys = ON
```

Migration：

```text
orphan preflight
+
unique conflict preflight
+
table rebuild if necessary
+
constraint validation
```

任何 Migration 错误：

```text
必须显式失败
```

不得：

```python
except:
    pass
```

---

## §51 P0 Implementation Order + Closure Rule

最终实施顺序：

### P0-1

```text
BatchGenerateService
```

统一 Batch 双入口。

### P0-2

```text
StepCanonicalizer
CodeValidator
GeneratedCode.source_steps_hash
```

建立代码来源闭环。

### P0-3

```text
AI timeout
retry
attempt log
limiter
```

解决 2026-09-24 已经暴露出来的：

```text
AI read timeout
导致长时间 polling
```

问题。

### P0-4

```text
Pull Workers
Supervisor
```

### P0-5

```text
Circuit
Deadline
First-generation KPI
BatchRecord
```

### P0-6

```text
ExecutionAdmissionService
Manifest
ExecutionCase
RuntimeState
ExecutionCodeResolver
ExecutionStep
```

### P0-7

```text
CaseStateResolver
terminal_reason
Execution finalization
```

### P0-8

```text
Case-level Heal
HealRound
HealRecord.attempts
candidate persistence
finalization transaction
```

### P0-9

```text
Persistent Stop
Recovery
Frontend Stop Contract
Frontend Result Contract
```

### P0-10

```text
Execution Seal
Report
Report Idempotency
85% KPI
Progress
```

### P0-11

```text
Alembic
Legacy Bridge
RESTRICT FK
SQLite PRAGMA
Migration Preflight
```

---

# 六、最终验收标准

这套架构只有在下面全部成立以后，才算真正完成。

```text
TestCase 改动
↓
旧代码不会错误复用
```

```text
Execution 创建
↓
定义完整冻结
```

```text
Execution 执行
↓
绝不重新读取当前 TestCase 作为执行定义
```

```text
Healing
↓
不会读取 latest code 污染当前 Execution
```

```text
Failed Heal Candidate
↓
不会污染 GeneratedCode
```

```text
Stop
↓
不会继续开启新的业务动作
```

```text
Crash
↓
Recovery 能区分 stopped / interrupted
```

```text
Case 状态
↓
只由 Resolver 解释
```

```text
Execution failed
↓
只表示执行基础设施 / 数据完整性等级故障
```

```text
Report
↓
只读取 Execution 历史事实
```

```text
Frontend
↓
与 Resolver 共享同一状态语义
```

```text
Migration
↓
只有 Alembic 能改变 Schema
```

---

# 七、最终架构全景图

最终 AutoPilot 的核心数据流可以固定成：

```text
                 ┌──────────────────────┐
                 │      TestCase        │
                 │  当前业务定义         │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │  StepCanonicalizer   │
                 │      SHA-256         │
                 └───────┬───────┬──────┘
                         │       │
             steps_hash  │       │
                         ▼       ▼
               ┌────────────┐  ┌─────────────┐
               │GeneratedCode│  │  Manifest   │
               │source_hash  │  │ steps_hash  │
               └──────┬──────┘  └──────┬──────┘
                      │                 │
                      └───────┬─────────┘
                              ▼
                    ┌───────────────────┐
                    │ ExecutionAdmission│
                    │    All-or-None    │
                    └─────────┬─────────┘
                              ▼
                    ┌───────────────────┐
                    │    Execution      │
                    └───────┬───────────┘
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
       ExecutionCase   RuntimeState   ExecutionStep[]
              │             │              │
              │             ▼              │
              │      active_code_id       │
              │             │              │
              │             ▼              │
              │   ExecutionCodeResolver   │
              │             │              │
              └─────────────┼──────────────┘
                            ▼
                    Playwright Execute
                            │
                            ▼
                    CaseStateResolver
                            │
               ┌────────────┴────────────┐
               ▼                         ▼
          CaseResult               terminal_reason
               │                         │
               └────────────┬────────────┘
                            ▼
                    Execution Finalizer
                            │
                            ▼
                     Execution Seal
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
              Report                Metrics
                 │                     │
                 └──────────┬──────────┘
                            ▼
                         Frontend
```

Healing 则嵌入 Execution 内部：

```text
Execution
   ↓
Case failed
   ↓
Stop Intent ? ── Yes ──→ 不再新建 Heal
   │
   No
   ↓
Root Failed Step
   ↓
Heal Round
   ↓
Attempt ≤ 3
   ↓
AI Candidate
   ↓
CodeValidator
   ↓
Execute Case
   ├── Failed → 下一 Attempt
   └── Success
          ↓
   GeneratedCode
          ↓
   active_code_id
          ↓
   Case Continue
```

---

# 八、最终收敛判定

本轮重新验收后的结论：

```text
结构性逻辑漏洞：0
前后矛盾：0
核心状态闭环：成立
代码来源闭环：成立
Execution 历史冻结：成立
Heal 版本闭环：成立
Stop / Recovery 闭环：成立
Report 事实源闭环：成立
Frontend 契约：成立
Migration 权威唯一：成立
```

本次只剩：

```text
D1 Mock Admission 明确定义
D2 Circuit Scope 明确定义
D3 Stop 与 in-flight Heal 的边界明确
D4 KPI 空 Cohort = N/A
```

这四项都属于：

```text
实现级明确化
```

而不是：

```text
新的架构漏洞
```

因此不再开新的架构版本。

从现在开始，任何新问题统一归类为：

```text
① 实现发现
② 回归测试发现
③ 明确需求变更
```

而不是重新推翻这套架构。

这就是 AutoPilot V1.1 当前应当冻结并进入真正编码阶段的 Master Spec。

这一版的核心变化，是把**“原方案为什么不可靠”→“每个漏洞怎么修”→“现在的设计为什么闭环”→“最终代码具体按照什么规则实现”**四层全部串起来了。立项书原本要求的轻量 MVP 闭环也没有被这轮治理架空。

尤其值得保留的两个细节就是你刚刚指出的 **C1：`created_at DESC, id DESC`** 和 **C2：完整前端状态契约**；它们不是新架构，但确实必须进入最终实施文档，否则单独交给开发时很容易被遗漏。
