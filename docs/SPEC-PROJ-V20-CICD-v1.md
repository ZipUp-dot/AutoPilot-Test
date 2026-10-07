# FEATURE-SPEC · PROJ-V20-CICD · 可视化 CI/CD 集成

> 状态：**FROZEN**（Owner 2026-10-07 15:25，无新增决策点） · 2026-10-07 · 依赖：PROJ-V20-SCHED（同批施工，先行）
> 输入：Frozen Spec V9.8.1 · v2.1 §十二 · Matrix #3 · 立项书 V2.0

## 1. Feature Definition

**范围**：三层能力——
① 流水线实体：把"一次发布验证"建模为 pipeline（多个 execution 的有序/并行集合）；
② 外部触发入口：HTTP API 触发（供 GitLab/Gitee Webhook 或任意 CI 调用），走 Admission 唯一入口；
③ Web 可视化：流水线运行视图（阶段/任务/耗时/通过率）+ 触发配置。
**非目标**：不做 Git 平台深度集成（commit 状态回写、MR 评论等 v3 再议）；
不做制品构建/部署（本平台只管测试验证段）。

## 2. Existing Code Audit

**复用**：ExecutionAdmission/Execution/orchestrator 全链（触发只调正式入口）；
SCHED 的调度循环模式（同构，触发器共用执行请求构造）；metrics_service 聚合模式。
**新建**：pipelines/pipeline_runs 表 + trigger API + 前端流水线视图。当前零实现。

## 3. Data Model（Schema Delta = alembic_revision_required，迁移 0006）

**新表 `pipelines`**：id / project_id FK / name / trigger_config_json
（schedule_id 可空关联 / webhook_token 唯一可空 / manual=true）/
stages_json（阶段定义：每阶段 = {name, case_selector, env}）/
enabled / created_at。
**新表 `pipeline_runs`**：id / pipeline_id FK / trigger_type
（schedule/manual/webhook）/ trigger_detail / status
（queued/running/success/failed，**派生态由 executions 汇总，不写独立状态机**）/
started_at / finished_at。
索引：idx_pipe_project、idx_prun_pipeline(started_at)。

## 4. State Model

pipeline_run.status 是 **Derived**（writer 无）：由下属 executions 的终态汇总得出，
唯一 writer = 汇总任务（每 execution Seal 后重算）。禁止独立状态机（v2.1 §四.4）。
pipelines 生命周期 enabled⇄disabled 同 SCHED 语义。

## 5. Data Flow

```
触发源（SCHED 到点 / 手动按钮 / Webhook 带 token）
 → POST /api/v1/pipelines/{id}/trigger（校验 token/权限）
 → 创建 pipeline_run + 按 stages_json 逐阶段发起 execution 请求
 → ExecutionAdmission（唯一入口）→ Execution → Seal
 → 汇总任务：全部终态后派生 pipeline_run.status → 前端视图
```

## 6. Backend

- models + 迁移 0006（只走 Alembic）；
- `services/pipeline_service.py`：trigger（构造执行请求、防重入 = 同一 pipeline
  已有 running run 时拒绝并 409）、derive_status（汇总）；
- `routers/pipelines.py`：CRUD + trigger + runs 列表；
- Webhook 安全：`X-Pipeline-Token` 头比对 pipelinewebhook_token（常量时间比较），
  token 只在创建时返回一次；
- 汇总挂在 execution Seal 后的既有钩子点（不新增状态出口，不变量 #7/#8）。

## 7. Frontend

- `src/views/PipelineManage.vue`：流水线列表 + 编辑（阶段编辑器：增删阶段、选用例范围）；
- `src/views/PipelineRuns.vue`：运行视图（阶段泳道、每阶段 execution 状态/耗时、通过率徽标）；
- api/schedules 同构新增 api/pipelines.js；webhook URL+token 展示（可复制）。

## 8. Integration

统一链：Web 配置 → 触发 → ExecutionAdmission → Execution → Report（§十二）；
**禁绕 Admission、禁第二 Execution Contract**（C-22 教训：触发只构造请求，不直接建 execution 事实）；
Report 已含单 execution 报告；pipeline 级报告 = 视图聚合，不新建报告体系。

## 9. KPI Impact

Original KPI 不改；Engineering Metrics：流水线一次通过率（派生）。

## 10. Schema Delta

alembic_revision_required：0006_pipelines（两表 + 索引）。零 backfill。

## 11. Acceptance Criteria

| AC | 内容 | acceptance_method |
|---|---|---|
| AC-01 | 非法/缺失 token 的 webhook 拒绝（401）且不产生 run | 单测 |
| AC-02 | 触发走 Admission（Source 证据：调用链含 execution_admission_service） | 集成测试 |
| AC-03 | running 中再次触发 → 409，不产生第二 run | 单测 |
| AC-04 | run.status 与下属 executions 终态一致（派生正确） | 集成测试：构造混合终态 |
| AC-05 | pipeline 视图数据与逐 execution 数据口径一致 | 接口测试（同 CaseStateResolver 口径） |
| AC-06 | 全量回归 GREEN + 迁移 alembic 级证据 | 硬边界增补 4 |

## 12. Acceptance Tests

tests/unit/test_pipeline_token.py / tests/services/test_pipeline_service.py
（trigger/防重/派生）/ tests/routers/test_pipelines.py；RED 先行。

## 13. Risk Audit

| 风险 | 对策 | 依据 |
|---|---|---|
| 触发入口变第二 Execution Contract | 只构造请求，execution 由 Admission 创建 | §十二；C-22 |
| run.status 变独立状态机 | Derived 汇总 + AC-04 | §四.4 |
| webhook 暴力枚举 token | 常量时间比较 + 失败计数限速 | 工程经验 |
| 与 SCHED 重复造触发器 | 共用执行请求构造函数（放 admission 邻近） | 7.6 |

## 14. File Modification Scope

RED 测试 → 0006 迁移 → models → pipeline_service → routers → 前端（2 views+api+store）
→ GREEN → Smoke → commit feat(pipeline): 可视化 CI/CD 集成（PROJ-V20-CICD）。
Owner Decisions：无新增（D-5/D-6 属 SCHED；如 SCHED 未批，本 Feature 的触发调度部分同样适用其结论）。
