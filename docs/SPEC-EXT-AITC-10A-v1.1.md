# FEATURE-SPEC · EXT-AITC-CHAIN-10A · AI TestCase 后端链

> 状态：**FROZEN**（Owner 2026-10-07 11:05，随梭哈批） · 2026-10-07
> 输入：Frozen Spec V9.8.1 · v2.1 第 4 层 8.1–8.8（已接收）· Matrix #10（已裁定）
> · BASE `5b1c903` + DEBT-SLOT-ASYNC（main `080d59c`）
> 姊妹篇：10B（审核前端）独立出设计，本设计为其提供 API 契约

## 1. Feature Definition

**范围**：实现 v2.1 第 4 层链路的**后端全部环节**：
Page Elements → Evidence Snapshot（固化）→ Evidence Sanitizer → AI Candidate
→ Draft（版本化持久化）→ System Validation（三维之一）→（10B 的人工评审态）
→ Promote → TestCase（source=ai_draft）→ 既有 BatchGenerate/Admission/Execution 链。

**非目标**：前端审核页（10B）；AI Candidate 的多轮对话优化；用例执行（复用既有链）；
自动 Promote（禁止，8.8）；修改 StepCanonicalizer/hash 语义（8.8 明令）。

## 2. Existing Code Audit

**复用**：
- `element_extractor` + `element_service`：真实抓取链（#7 已核验存在），作为 Evidence 来源；
- `ai_service`：新增一个生成方法，**必须复用** `_chat_http_attempt`（已根治超时 + 异步 slot），
  天然满足 §13（1 Attempt=1 Quota=1 Slot）与超时治理；
- `step_canonicalizer.hash_steps`：Draft 一致性校验的哈希工具（只读使用）；
- `case_service`：Promote 时创建 TestCase 走既有服务，不重写 CRUD。

**不复用/需新建**：Evidence 快照无实体（page_elements 每次 crawl 全量重建且无 crawl 批次 ID，
无法回答"这份 Draft 基于哪次抓取"——这是本 Feature 的第一动因）；Sanitizer 零实现
（TRAE 第 4 层核验 C 系列已证）；Draft/三维评估/Promote 零实现（8/11 环节缺失）。

## 3. Data Model（Schema Delta = alembic_revision_required，新增迁移 0004）

**新表 1：`evidence_snapshots`**
| 列 | 类型 | 约束 |
|---|---|---|
| id | INT | PK |
| project_id | INT | FK→projects.id，RESTRICT |
| source_url | VARCHAR(512) | 非空 |
| snapshot_hash | VARCHAR(64) | 非空（本次抓取全部元素规范化后的 SHA256） |
| element_count | INT | 非空 |
| crawl_timestamp | DATETIME | 非空 |
| created_at | DATETIME | 默认 now |

**新表 2：`ai_case_drafts`**
| 列 | 类型 | 约束 |
|---|---|---|
| id | INT | PK |
| project_id | INT | FK，RESTRICT |
| snapshot_id | INT | FK→evidence_snapshots.id，RESTRICT |
| draft_key | VARCHAR(64) | 逻辑草稿标识（同一用例的多版本共享） |
| draft_version | INT | ≥1；UNIQUE(project_id, draft_key, draft_version) |
| case_name / priority / preconditions / expected_result | — | 与 test_cases 同型 |
| steps | TEXT(JSON) | 非空 |
| ai_assessment | VARCHAR(20) | recommended / needs_review / not_ready |
| validation_status | VARCHAR(10) | valid / invalid |
| validation_errors | TEXT(JSON) | nullable |
| review_status | VARCHAR(20) | pending / needs_edit / approved / rejected |
| review_comment | TEXT | nullable（10B 写入） |
| promoted_case_id | INT | FK→test_cases.id，nullable，终态后回填 |
| created_at / updated_at | DATETIME | — |

**改表 3：`page_elements` 增列** `snapshot_id INT NULL FK→evidence_snapshots.id`
（新抓取先建 snapshot 再写元素；历史行 NULL=legacy，不回填——历史事实不 hard delete，不变量 #11）

**改表 4：`test_cases` 增列** `source VARCHAR(16) NOT NULL DEFAULT 'excel'`
（CHECK ∈ {excel, ai_draft}；仅 provenance，**不进 StepCanonicalizer / source_steps_hash**，8.8 钉死）

索引：idx_draft_review(project_id, review_status)、idx_draft_snapshot(snapshot_id)、
idx_snap_project(project_id, crawl_timestamp)。

## 4. State Model

Draft 生命周期（writer 唯一：`ai_case_service`；reader：10B 前端 + Promote 入口）：

```
AI 生成+系统校验 → pending_review ──needs_edit(人工)──→ 新 draft_version（旧行终态冻结）
                     │                                        │
              approved(人工, 10B)                       rejected（终态）
                     │ Promote 前置复验（对当前有效 snapshot）
                一致 → 创建 TestCase → 本行终态（promoted_case_id 回填）
                不一致 → review_status=needs_review（8.2：禁止自动拒绝）
```

- 终态：promoted / rejected / 被新版 supersedeed（旧版本行，非删除）；
- Derived status 不伪装状态机：overview 计数只读派生，不持久化。

## 5. Data Flow

```
抓取（既有 element_extractor，改：先建 snapshot，元素带 snapshot_id）
 → SnapshotHash 计算（元素集规范化 SHA256）
 → Sanitizer（脱敏后元素 + 既有 Prompt 模板）→ ai_service 生成方法（复用 _chat_http_attempt）
 → JSON Schema 校验（8.4：schema_version/case_name/priority/preconditions/steps/
   expected_result/ai_assessment；step 含 evidence_ref/evidence_snapshot_id）
 → System Validation（valid/invalid + 错误明细；三维独立存储）
 → ai_case_drafts 持久化（draft_version=1）
 → [10B 人工：编辑→新版本 / 批准 / 拒绝]
 → Promote：valid ∧ approved ∧ snapshot 复验 → case_service 创建 TestCase(source=ai_draft)
 → 既有 BatchGenerateService → GeneratedCode → ExecutionAdmission → Execution（零改动）
```

## 6. Backend

- `utils/evidence_sanitizer.py`：7 类规则（email/phone/token/api key/cookie/session ID/
  authorization/其他敏感字段）+ 通用键名表；输入输出均为元素 JSON；**先于 Prompt**（8.3）；
- `models/evidence_snapshot.py` / `models/ai_case_draft.py`：ORM + Pydantic；
- `services/ai_case_service.py`：生成/校验/版本/评审写入/Promote 全部入口；
  Promote 复验逻辑：draft.snapshot_hash vs 当前有效 snapshot（同 project 最新 crawl）的 hash；
- `ai_service.py`：新增 `generate_test_cases(prompt) -> str`，内部仅复用既有调用链；
  新增 Prompt 模板 `prompts/generate_case_prompt.txt`（含元素上下文注入位 + JSON Schema 要求 +
  action 白名单——白名单复用 excel_parser 既有允许集）；
- `element_extractor` 抓取链：开头建 snapshot、元素落 snapshot_id（改动面最小化）；
- `routers/ai_drafts.py`：POST /generate（起生成）、GET 列表/详情、PUT /{id}/review
  （人工三维写入）、POST /{id}/versions（编辑产生新版本）、POST /{id}/promote；
- `case_service`：create 支持 source 字段透传（默认 excel，行为不变）。

## 7. Frontend

无（全部在 10B；本设计输出 API 契约供 10B 消费）。

## 8. Execution/Heal/Report/Metrics Integration

- Promote **不创建** Execution / ExecutionStep / GeneratedCode（8.8）；
- 执行走既有 Admission 唯一入口——无第二 Execution Contract（12 条口径）；
- Report 依赖 CaseStateResolver 不变：ai_draft 来源的 case 与 excel case 同权进入报告；
- Metrics：overview 增加派生计数（draft 批准率等），只读派生。

## 9. KPI Impact

- Original KPI：口径不改。预期正向影响 KPI-01（AI 首生成率，因用例本身由 AI 生成、
  步骤与元素证据同源）；不承诺数字（不变量 #2，验收时以实测登记 Engineering Metrics）；
- Engineering Metrics：draft 一次批准率、Promote 后首次生成成功率（独立列，不回写 KPI）。

## 10. Schema Delta

`alembic_revision_required`：新建 0004_ai_case_drafts（上述 2 表 + 2 改列 + 索引 + CHECK 约束；仓库现有链 0001→0002→0003_batch_jobs，0003 已占用）。
Backfill：仅 test_cases.source 默认值（'excel'，零数据迁移）；page_elements.snapshot_id
保持 NULL（legacy 语义）。只走 Alembic（§十五），legacy bridge 不参与。

## 11. Acceptance Criteria

| AC | 内容 | acceptance_method |
|---|---|---|
| AC-01 | Sanitizer 先于 Prompt 且 7 类敏感值全部脱敏 | 单测：注入含 7 类样本的元素集，断言进入 prompt 前已替换为占位符 |
| AC-02 | AI 输出 JSON Schema 校验：缺字段/非法 action/自造 action → invalid + 明细 | 单测：3 组坏样本 RED→GREEN |
| AC-03 | 三维独立存储，永不合并：ai_assessment/validation_status/review_status 分列 | 单测 + DB 断言三列可独立取值 |
| AC-04 | Promote 前置：valid ∧ approved ∧ snapshot 复验一致 → 创建 TestCase(source=ai_draft) | 集成测试：DB 断言 test_cases 新行 source 值 |
| AC-05 | snapshot 复验不一致 → review_status=needs_review，**不自动拒绝、不 Promote** | 单测：改当前 snapshot 后复验 |
| AC-06 | Excel 入口回归：导入/生成/执行链路不受本 Feature 影响 | 既有 1492 全量 GREEN 不降级 |
| AC-07 | Promote 不产生 Execution/ExecutionStep/GeneratedCode | 集成测试断言三表无新行 |
| AC-08 | Draft 版本语义：needs_edit → 新版本行，旧 approval 不可复用 | 单测：v1 approved 后 needs_edit → v2 pending，v1 approval 失效 |
| AC-09 | source 不进入 canonicalizer：source_steps_hash 计算与 source 无关 | 单测：同 steps 不同 source → hash 相同 |

## 12. Acceptance Tests（AC→Test 映射）

新测试：`tests/unit/test_evidence_sanitizer.py`（AC-01）、
`tests/unit/test_ai_case_service.py`（AC-02/03/05/08/09）、
`tests/services/test_ai_case_promote.py`（AC-04/07）、
`tests/routers/test_ai_drafts.py`（API 契约，供 10B 对齐）、
`tests/integration/test_aitc_chain.py`（端到端：抓取→生成→评审→Promote→执行链挂载，全 mock AI）。
AC-06 = 既有全量回归。新行为全部 RED 先行。

## 13. Risk Audit（引用 Frozen Spec / v2.1 规则 ID）

| 风险 | 对策 | 依据 |
|---|---|---|
| 新 AI 调用绕过超时/限流治理 | 强制复用 `_chat_http_attempt`（含 slot 异步化） | §13/§14；不变量 #9 |
| source 污染步骤哈希 | source 仅 provenance 列；AC-09 锁定 | 8.8 |
| snapshot 术语撞 Frozen Spec 的 Manifest snapshot | 全名 evidence_snapshot_*（域限定符，C-17 裁定规则） | C-17 已裁定 |
| Promote 绕 Admission | Promote 只到 TestCase，执行必须走既有入口 | 8.8 / §十二 |
| page_elements 改列影响抓取重建语义 | snapshot_id 可空、仅追加 | 代码事实 |
| 历史元素行 snapshot_id=NULL 被误当"无证据" | 文档注明 legacy 语义；新功能仅消费非 NULL 行 | 不变量 #11 |
| Draft 表成为第二 TestCase 事实源 | TestCase 唯一事实源不变；Draft 是施工态（第 4 层语义） | 7.6 第二事实源禁令 |

## 14. File Modification Scope

| 顺序 | 文件 | 改动 |
|---|---|---|
| 1 | tests（§12 五个新文件，RED） | 新增 |
| 2 | alembic/versions/0004_*.py | 新增迁移 |
| 3 | app/models/evidence_snapshot.py / ai_case_draft.py | 新增 |
| 4 | app/utils/evidence_sanitizer.py | 新增 |
| 5 | app/services/ai_case_service.py | 新增（核心） |
| 6 | app/ai_service.py 增 generate_test_cases；prompts/generate_case_prompt.txt | 追加 |
| 7 | app/services/element_extractor（抓取链挂 snapshot） | 小改 |
| 8 | app/models/page_elements.py / test_case.py 增列；case_service 透传 source | 小改 |
| 9 | app/routers/ai_drafts.py + main.py 注册 | 新增 |
| 10 | 全量 GREEN → Smoke 四件套 → commit feat(ai): AI TestCase 后端链（10A） | — |

**Owner Decisions 待裁**：
- D-1 生成数量策略：一次生成全页用例 vs 按元素分区多次生成（默认后者，防超长输出）；
- D-2 Draft 编辑（10B 的 needs_edit 产生新版本）是否允许改 steps 结构本身
  （默认允许，改结构=新版本+重新 System Validation）；
- D-3 迁移 0003 与 10B 是否同批合并（默认同批，一次 migration）。
