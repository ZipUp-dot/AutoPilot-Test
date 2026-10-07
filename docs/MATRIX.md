# MATRIX · 需求追溯矩阵（FROZEN）

> 性质：Derived Artifact（v2.1 §1.4）。**状态 = FROZEN（Owner 裁定：11 行全进，2026-10-07 09:36）**。
> MATRIX_SOURCE_SHA 随本文件入库 commit 锁定；立项书 V2.0 = Owner 本地证据（sha256 55aa0fd8…e691，Owner 决策不入库）。
> 证据时间：2026-10-07 · 基线 `5b1c903` · 立项书 sha256 `55aa0fd8…e691`

## 需求清单（Owner 已裁定：全进）

| # | requirement_id | source | 需求 | current_state（代码证据） | gap | dependency | acceptance（草案） |
|---|---|---|---|---|---|---|---|
| 1 | PROJ-V11-ANDROID | Proposal·立项书§6.2 | Android UI 自动化 | ✅ 已实现：appium_service.py / android_crawl_service.py 在场 | 真机验证缺失（三态：real_device_validated=PENDING） | 真机环境 | 真机冒烟记录 + 三态标注 |
| 2 | PROJ-V11-REPORT | Proposal·立项书§6.2 | 更丰富的测试报告 | ✅ 已实现（README：V1.1 Core 含 Report 增强/Heal History） | 无 | — | 报告字段核对验收报告 |
| 3 | PROJ-V20-CICD | Proposal·立项书§6.2 | 可视化 CI/CD 集成 | ❌ 无代码（grep schedule/cron 零命中） | 全量 | #4 Schedule、#6 User | Web 配置→触发→Admission→Report 链路验收 |
| 4 | PROJ-V20-SCHED | Proposal·立项书§6.2 + v2.1 §十二 | 定时执行（Schedule） | ❌ 无代码 | 全量 | #1 之前无 | Schedule Stop ≠ Execution Stop 语义分离验证 |
| 5 | PROJ-V20-USER | Proposal·立项书§6.2 + v2.1 §十三 | 用户管理（Auth/RBAC） | ⚠️ 仅 INTERNAL_API_TOKEN 过渡鉴权（files.py），无 User/Role | 全量 | — | 后端强制权限（前端隐藏按钮不算安全控制） |
| 6 | PROJ-V20-MOCK | Proposal·立项书§6.2 + v2.1 §十四 | Mock 服务集成 | ❌ 无代码 | 全量 | — | MockServer 与 AndroidMockDriver 严格分离 |
| 7 | EXT-V12-AICRAWL | Extension·README V1.2 | AI 感知页面抓取（goto 失败→截图分析→前置操作） | 🟡 部分：crawl_analyze.txt + element_service.py 在场 | 实现程度待逐行核验 | — | goto 失败场景 E2E 验收 |
| 8 | EXT-V12-HEALCOST | Extension·README V1.2 | 自愈成本防护（健康检查/同类错误快败/限流熔断） | 🟡 部分：config 有 HEAL_MAX_RETRY_SAME_ERROR / PRE_EXECUTION_CHECK | 实现程度待逐行核验 | — | 防护触发场景测试 |
| 9 | EXT-V12-AGGREG | Extension·README V1.2 | 执行列表实时聚合 | 🟡 部分：metrics_service.py 有 overview/成功率/覆盖率 | 前端聚合契约待核验 | — | 前后端口径一致性（Spec §45/51） |
| 10 | EXT-AITC-CHAIN | Extension·v2.1 §八 | AI TestCase 生成链（Evidence→Sanitizer→Draft→三维评估→Promote） | ❌ 无代码（ai_draft/evidence_snapshot 零命中） | 全量 | #7 元素抓取 | 三维永不合并 + Promote 幂等验收 |
| 11 | DEBT-SLOT-ASYNC | TechDebt·RETRO-HOTFIX-001 §7 | acquire_slot 改 asyncio 语义 | ❌ 未开工（threading.BoundedSemaphore 阻塞 event loop 隐患） | 全量 | 无（可首批施工） | 并发回归 + 限流器语义测试不降级 |

## 裁定规则

- 你逐条回复：✅ 进入 Matrix / ❌ 剔除 / ✏️ 修改；
- requirement_source 已按 {Proposal, Extension, Bugfix, TechDebt} 分类，Extension 不计入 Proposal Gap（v2.1 §1.4）；
- 原始 KPI 四项（≥70% / ≥85% / ≤60s / ≥100 行）不在本表——口径已永久冻结，只作验收基准，不是 Feature；
- 本表已 FROZEN；Feature 入 Queue 需各自完成 Feature Design + Owner Freeze（Gate D 局部）。

## 已知缺口声明

- Master Plan v1.4 不存在（Owner 确认功能为即兴提出），以本表 + 立项书共同构成需求全集；
- #7/#8/#9 的 current_state 仅做了存在性扫描（grep 级证据），施工前需逐行核验实现完整度——届时以 Source Evidence 更新 current_state。

## Build-B 增量（Owner 2026-10-07 11:45 并入，MATRIX_SOURCE_SHA 随提交 commit 更新）

| feature_id | source | 需求 | current_state | gap | dependency | acceptance |
|---|---|---|---|---|---|---|
| EXT-V12-AGGREG-FE | Extension | 执行列表 KPI 聚合前端接线 | 后端✅ metrics_service / 前端❌ 零引用 | 前端接线 | 无 | 四 JSONPath 渲染一致（VERIFY-789 #9） |
| BUG-REPORT-RESOLVER | Bugfix(C-21) | Report 状态解释统一走 CaseStateResolver | Derived 证据，待节拍 0 定位 | 自判分支清理 | 无 | Spec §11 AC-01~03 |
| BUG-SECOND-CONTRACT | Bugfix(C-22) | 第二 Execution Contract 处置 | HOLD：原标的文件不存在，待 F4 重定位 | 重定位后修订 Spec | F4 dual-task | 修订后定 |
| DEBT-DEAD-CONFIG | TechDebt | 删除死配置 HEAL_SKILL_ENABLED | Source 证据：config.py 定义处，全仓零使用 | 删除 | 无 | grep 零使用证据 |
| DEBT-PRIVATE-API | TechDebt | _chat_http_attempt 私有名跨模块复用治理 | Source：多模块引用私有名 | 下批处理 | — | 下批定 |
