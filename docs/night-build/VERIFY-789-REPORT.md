# #7/#8/#9 核验报告（Matrix current_state 证据升级）

> 证据类型：Source Evidence（逐行代码核验）· 基线 5b1c903 · 2026-10-07
> 核验人：Kimi · 性质：只读分析，零代码改动

## #7 EXT-V12-AICRAWL · AI 感知页面抓取 —— ✅ 已实现（可转验收）

| 要素 | 证据 |
| --- | --- |
| 核心实现 | `element_service._ai_assisted_navigation()`（:289-335）：goto 失败 → 截图 → Vision API 分析 → 执行前置操作 JSON |
| Prompt 接线 | `prompts/crawl_analyze.txt` 经 :304 读入，缺失时有默认 Prompt 兜底 |
| 调用点 | `element_service.py:244`：抓取流程内 goto 失败 → 调 AI 导航 → 重试 |
| Vision 依赖 | `_call_openai_vision`（ai_service.py 提供，经超时根治版本） |

结论：README V1.2 声称的"goto 失败自动截图分析并执行前置操作"**代码层完整落地**。
遗留验收面：无单测直接覆盖 _ai_assisted_navigation（grep 未见 test 引用），
建议验收时补 1-2 个 mock Vision 的单测——列为验收前置项，非施工。

## #8 EXT-V12-HEALCOST · 自愈成本防护 —— 🟡 大体已实现（1 个死配置）

| 防护层 | 证据 | 判定 |
| --- | --- | --- |
| 环境健康检查 | `heal_service` 0a 步（:95-130 区段）：0a-1 base_url SSRF 检查 + 0a-2 首页可达性探测，orchestrator :330+ 执行前检查 | ✅ |
| 同类错误快速失败 | `HEAL_MAX_RETRY_SAME_ERROR`（config）+ heal 内同类错误阈值跳 Round | ✅ |
| 限流/熔断 | AI Rate Limiter（quota+slot，Spec §13）全局兜底 | ✅ |
| HEAL_SKILL_ENABLED | config.py:71 定义，**全仓零使用** | ⚠️ 死配置 |

结论：防护链实质存在。**唯一债务**：HEAL_SKILL_ENABLED 死配置——建议并入 Matrix
TechDebt 行（清理或启用，二选一由 Owner 定），不值得单独开 Feature。

## #9 EXT-V12-AGGREG · 执行列表实时聚合 —— 🟡 后端已备，前端未接线（真 gap）

| 层 | 证据 | 判定 |
| --- | --- | --- |
| 后端 | `metrics_service.py`：overview（first_generation_success_rate / final_success_rate / coverage）+ /metrics 路由 | ✅ |
| 前端 | `frontend/src/api/` 与 `views/` **零命中 metrics**（grep 实证） | ❌ 未接线 |

结论：这是 Matrix 中唯一的真 gap——后端 KPI 聚合 API 已就绪，前端没有消费。
工作量小（一个 view + api 模块 + store 字段），但按协议它就是一个独立 Feature
（EXT-V12-AGGREG-FE），建议并入本次梭哈 Queue，排在 10A/10B 之后。

## Matrix 增量与状态修订（待 Owner 一并裁定）

| 行 | 动作 | 内容 |
| --- | --- | --- |
| #7 | 状态修订 | 🟡 部分 → ✅ 已实现（附验收前置：补 mock Vision 单测） |
| #8 | 状态修订 | 🟡 → ✅ 大体实现；HEAL_SKILL_ENABLED 死配置并入 TechDebt 清理行 |
| #9 | 状态修订 + 拆分 | 🟡 → 后端✅/前端❌；拆出新行 EXT-V12-AGGREG-FE（前端接线） |
| 新增 | BUG-SECOND-CONTRACT（C-22） | 第二 Execution Contract 处置，Bugfix，代码级，走 Freeze |
| 新增 | BUG-REPORT-RESOLVER（C-21） | Report 状态真源/自判兜底口径修正，Bugfix，走 Freeze |
| 新增 | DEBT-DEAD-CONFIG | HEAL_SKILL_ENABLED 死配置清理，TechDebt，随批次顺手做 |