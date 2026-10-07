# FEATURE-SPEC · EXT-AITC-10B · AI 用例审核前端

> 状态：**FROZEN**（Owner 2026-10-07 11:05，随梭哈批） · 2026-10-07 · 姊妹篇：10A（后端链，D-1/2/3 已裁定）
> 输入：Frozen Spec V9.8.1 · v2.1 8.5/8.6/8.7 · 10A API 契约 · Spec §51 前端契约

## 1. Feature Definition

**范围**：专门的"AI 用例审核"页面——Draft 列表（状态筛选/版本历史）→ 详情编辑器
（改名称/优先级/前置/步骤/期望结果）→ 人工三维写入（批准/驳回/需修改）→ Promote 触发。
**非目标**：不做执行触发（Promote 后由既有用例页走 BatchGenerate）；不做 AI 重新生成
按钮（v2 再说）；不做步骤的拖拽排序（编辑 JSON 级步骤即可，复杂度不值得）。

## 2. Existing Code Audit

**复用**：Element Plus 组件体系、frontend api 封装模式（8 个 api 模块范式）、
Pinia store 模式、项目/用例既有视图的路由与布局（侧边栏菜单注册方式）。
**新建**：本页面全部新建——现有前端对 ai_draft 零引用（TRAE 第 4 层核验实证）。

## 3. Data Model

无前端私有状态模型；全部状态来自 10A API（后端事实源 ≠ 前端展示状态，
v2.1 §四.7）。store 仅缓存列表响应，不派生状态。

## 4. State Model

不持有持久状态。页面内的瞬态（编辑中草稿/确认弹窗）用组件局部 ref；
review_status 的展示完全渲染后端值，前端不做状态机推断。

## 5. Data Flow

```
AiCaseReview.vue
 ├─ 列表区：GET /ai-drafts?project_id&review_status → 表格（draft_key/版本/assessment/
 │   validation/review 三列独立展示，图标区分）
 ├─ 详情区：GET /ai-drafts/{id} → 表单编辑器（steps 用结构化行编辑：action/target/
 │   locator/input/assertion + evidence_ref 只读徽标，点击高亮对应元素名）
 ├─ 保存编辑：POST /ai-drafts/{id}/versions → 后端产生新版本（D-2 已裁定）
 ├─ 人工三维：PUT /ai-drafts/{id}/review {review_status, review_comment}
 │   （approved 按钮二次确认，提示"Promote 后进入用例库"）
 └─ Promote：POST /ai-drafts/{id}/promote → 成功提示 + 跳转既有用例列表（source=ai_draft 徽标）
snapshot 复验不一致（后端返回 needs_review）：toast 提示"页面已变化，请复核"，列表刷新——
不自动拒绝（8.2）。
```

## 6. Backend

无改动（10A 已交付全部 API）。本设计只消费，契约如有缺口 → STOP 上报，禁改后端。

## 7. Frontend

| 文件 | 内容 |
|---|---|
| src/api/aiDrafts.js | 5 个端点封装（generate/list/detail/versions/review/promote） |
| src/views/AiCaseReview.vue | 主页面：左列表右详情双栏 |
| src/components/DraftStepEditor.vue | 步骤行编辑器（evidence_ref 只读） |
| src/stores/aiDrafts.js | 列表缓存 + 加载态 |
| src/router + 菜单 | 项目上下文内路由 /projects/:id/ai-review |

交互红线（8.7）：本页面**零副作用操作**——无 click/fill/submit 任何浏览器动作，
evidence_ref 仅做名称解析展示（read-only locator resolution 的展示侧）；
步骤里 locator 字段提供"在证据元素中查找"按钮（只读匹配，标绿/标红）。

## 8. Execution/Heal/Report/Metrics Integration

无。Promote 后的一切（生成代码/执行/报告/指标）走既有链，前端不挂旁路入口。

## 9. KPI Impact

Original KPI 不改。Engineering Metrics：审核页操作埋点不算（无埋点基础设施），
以 Draft 批准率（后端派生）为准。

## 10. Schema Delta

no_schema_change（纯前端 Feature）。

## 11. Acceptance Criteria

| AC | 内容 | acceptance_method |
|---|---|---|
| AC-01 | 三维三列独立渲染，互不覆盖 | 组件测试：mock 不同三维组合渲染断言 |
| AC-02 | 编辑保存产生新版本而非覆盖 | e2e mock：保存后列表出现 v+1，旧版本只读 |
| AC-03 | approved 需二次确认；rejected/needs_edit 必填 comment | 组件测试 |
| AC-04 | Promote 成功跳转用例列表且带 source 徽标 | e2e mock |
| AC-05 | snapshot 复验不一致 → toast + 刷新，不自动拒绝 | e2e mock 后端返回 needs_review |
| AC-06 | 页面无任何浏览器副作用调用 | 代码审查 + 网络面板断言（测试拦截所有非 API 请求） |

## 12. Acceptance Tests

`frontend` 测试基建现状：无组件测试框架——**Owner 决策点 D-4**：
A. 只做 e2e mock 级（vitest + msw 风格，轻量）
B. 引入组件测试（@vue/test-utils，新增基建）
默认推荐 A（与现有前端零测试的现状匹配，不为一个页面引入整套基建）。

## 13. Risk Audit

| 风险 | 对策 | 依据 |
|---|---|---|
| 前端自造 review 状态机 | store 只缓存；状态渲染后端值 | §四.7 / 8.5 |
| 审核页绕过后端校验直接改数据 | 全部写操作走 API；禁本地改行 | 8.6/8.7 |
| evidence 元素名解析在前端做 | 只做展示名匹配，不执行 locator | 8.7 read-only |
| 大 Draft 列表性能 | 分页（后端分页参数已具备） | 工程经验 |

## 14. File Modification Scope

顺序：api → store → 组件 → 视图 → 路由/菜单 → 测试（RED 先行）→ Smoke。
commit：feat(frontend): AI 用例审核页（10B）。
Owner Decisions：D-4 测试策略（推荐 A）。
