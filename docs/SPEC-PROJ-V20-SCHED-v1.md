# FEATURE-SPEC · PROJ-V20-SCHED · 定时执行（Scheduled Execution）

> 状态：**FROZEN**（Owner 2026-10-07 15:25，D-5=30s / D-6=croniter） · 2026-10-07 · 依赖：无（V2.0 四件套第一个）
> 输入：Frozen Spec V9.8.1 · v2.1 §十二 · Matrix #4 · BASE = Build-B 完成后的 main

## 1. Feature Definition

**范围**：Web 界面配置定时任务（cron 表达式 + 执行配置）→ 调度器到点触发
→ 走既有 ExecutionAdmission → Execution → Report（统一入口，禁止绕 Admission）。
**非目标**：CI/CD 外部触发（下一个 Feature）；调度器自身 HA（单 worker 前提不变）；
定时任务的并发防重依赖执行侧既有 admission 幂等。

## 2. Existing Code Audit

**复用**：ExecutionAdmission / orchestrator / execution_state 全链（唯一执行入口）；
batch_generate 的 Job 注册表模式可参考但不复用代码（域不同）。
**新建**：schedules 表 + 调度循环 + 管理 API + 前端管理页。当前代码零调度设施
（TRAE 规则核验实证：schedule/cron/apscheduler 零命中）。

## 3. Data Model（Schema Delta = alembic_revision_required，迁移 0005）

**新表 `schedules`**：
| 列 | 类型 | 约束 |
|---|---|---|
| id | INT | PK |
| project_id | INT | FK→projects.id，RESTRICT |
| name | VARCHAR(128) | 非空 |
| cron_expr | VARCHAR(64) | 非空（5 段标准 cron） |
| exec_config_json | TEXT | 非空（复用既有执行配置结构：环境/模式/用例选择） |
| enabled | BOOLEAN | 默认 true |
| last_run_at / next_run_at | DATETIME | nullable |
| last_execution_id | INT | FK→executions.id，nullable |
| stop_requested_at | DATETIME | **nullable，Schedule Stop 唯一权威（与 Execution 的 stop_requested_at 语义分离，v2.1 §十二）** |
| created_at / updated_at | DATETIME | — |

索引：idx_sched_project(project_id, enabled)、idx_sched_next(next_run_at, enabled)。

## 4. State Model

Schedule 生命周期：enabled ⇄ disabled（Owner 操作）；stop_requested_at 仅由
"停止调度"按钮写入（**不杀正在跑的 Execution**——Execution 的停止走既有
stop_requested_at 权威，不变量 #6 不冲突：两者属不同实体）。
调度器只读 enabled + next_run_at 触发，不写业务状态；Derived（下次运行倒计时）
前端算，不入库。

## 5. Data Flow

```
Web 配置 → schedules CRUD → 调度循环（每 30s tick，算 next_run_at）
 → 到点：创建 execution 请求 → ExecutionAdmission（唯一入口）→ Execution → Report
 → 回填 last_run_at / next_run_at / last_execution_id
"停止调度" → 写 schedules.stop_requested_at（= 置 disabled）→ 已在跑的 Execution 不受影响
```

## 6. Backend

- `models/schedule.py` + Pydantic；迁移 0005（只走 Alembic，§十五）；
- `services/scheduler_service.py`：async 后台循环（lifespan 启动，单 worker 内；
  每 30s tick 是既有 config 数字还是 Owner 显式给定 → **Owner 决策 D-5**，默认 30s 待批）；
  cron 解析用 `croniter`（Owner 已裁 D-6，版本固定）。
  触发时调 orchestrator 正式入口（**禁止直接调 execution_state**）；
- `routers/schedules.py`：CRUD + enable/disable + 手动触发一次（dry 真实执行，走 Admission）；
- `schemas.py`：ScheduleCreate/Update/Out；cron 表达式校验（5 段合法性）。

## 7. Frontend

- `src/views/ScheduleManage.vue`：任务列表（名称/cron 人类可读/下次运行/上次结果/启停开关）+
  编辑表单（cron 输入 + 在线校验 + 人类可读回显 + 执行配置复用既有执行配置组件）；
- `src/api/schedules.js` + store + 菜单项；启停开关二次确认。

## 8. Integration

统一链：Web Configuration → Schedule → ExecutionAdmission → Execution → Report（§十二）；
**无第二 Execution Contract**；Schedule Stop ≠ Execution Stop 在 API 层显式分离
（disable 接口文档注明"不影响运行中实例"）。

## 9. KPI Impact

Original KPI 不改。Engineering Metrics：调度触发成功率（派生自 executions，不回写）。

## 10. Schema Delta

`alembic_revision_required`：新建 0005_schedules（上表 + 索引）。零 backfill。

## 11. Acceptance Criteria

| AC | 内容 | acceptance_method |
|---|---|---|
| AC-01 | cron 非法表达式拒绝（422 + 明细） | 参数化单测：6 组坏 cron |
| AC-02 | 到点触发走 Admission 正式入口（Source 证据：调用链含 execution_admission_service） | 集成测试 mock |
| AC-03 | disable 后不再触发，且运行中 Execution 不被中断 | 集成测试：disable 时 running 实例状态不变 |
| AC-04 | 手动触发与定时触发同权（同一入口） | 代码审查 + 集成测试 |
| AC-05 | 全量回归 GREEN | 1492+N 基线不降级 |
| AC-06 | 迁移 0005 alembic 级证据（upgrade+downgrade） | 硬边界增补 4 |

## 12. Acceptance Tests

`tests/unit/test_cron_validation.py`（AC-01）、`tests/services/test_scheduler.py`
（触发逻辑 mock 时钟）、`tests/routers/test_schedules.py`、`tests/integration/test_schedule_admission.py`；
AC-03/04 集成级；全部 RED 先行。

## 13. Risk Audit

| 风险 | 对策 | 依据 |
|---|---|---|
| 调度循环成为第二执行入口 | 触发只调 orchestrator | §十二；7.6 |
| tick 间隔数字 AI 自定 | D-5 交 Owner，未批前不得实现 | 不变量 #2 |
| 新依赖 croniter 引入 | D-6 交 Owner | 工程经验 |
| schedules.stop 与 execution.stop 混淆 | 两实体字段分离 + AC-03 锁定 | v2.1 §十二；不变量 #6 |
| 重启后调度恢复 | lifespan 启动时按 next_run_at 重算（不盲目补跑错过窗口，只排未来） | §十七恢复语义类比 |

## 14. File Modification Scope

顺序：RED 测试 → 迁移 0005 → models → scheduler_service → routers → 注册 → 前端
（views/api/store/菜单）→ 全量 GREEN → Smoke → commit feat(sched): 定时执行（PROJ-V20-SCHED）。
**Owner Decisions：D-5（tick 间隔，默认 30s）；D-6（croniter vs 手写，推荐 croniter）**。
