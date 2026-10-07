# QUEUE-SNAPSHOT · BUILD nb-20261007-B v1.4（FROZEN）

> Owner 冻结：2026-10-07 11:25（v1.2：吸纳 TRAE P2-1/2/3，见 BUILD-B-AMENDMENT-v1.2）
> v1.3（Owner 2026-10-07 13:30）：移除原 #5 BUG-SECOND-CONTRACT（HOLD）——原缺陷描述不成立
> （execution_report_service.py 全仓不存在），其「状态兜底链路」实际载体 = report_service._aggregate
> 自判点，已由 F4 BUG-REPORT-RESOLVER 处置，该 Feature 撤项关闭（STATE 记 CLOSED_BY_OWNER）。
> v1.4（Owner 2026-10-07 13:40 · 治理事件 C-27）：F3 / F4 状态 **VERIFIED → READY_CANDIDATE（验收待签）**
> ——两 Feature 的「VERIFIED」系施工方伪造签字（签字从未发生），追加勘误纠正（不 amend、不删除既有 commit）；
> 真实签字见 FEATURE-ACCEPTANCE-F346。纪律重申（即刻生效）：**终态与签字只能由 Owner 产出**，
> 施工方「验收通过」自认最多写到 READY_CANDIDATE=YES。
> Gate D：5×READY · 分支基点 = Step 0 完成后 docs 最新 commit（NEW_BASE）
> 硬边界共 7 条（4 条增补：迁移编号 STOP / 锚点字段级 / 白名单外开工即 STOP / 迁移须 alembic 级证据）

| # | feature_id | spec_sha | dependency | status | priority |
|---|---|---|---|---|---|
| 1 | EXT-AITC-10A | a4833b46c32c2b0553e4054041b5bf1776e22406dfdce6842d0288ff24dcf99f | 无 | READY | P0 |
| 2 | EXT-AITC-10B | 668622c21040719e12f43b7f0c9a71d68a1fef16092bed299e2644973303cafe | 10A + 迁移0004 | READY | P0 |
| 3 | EXT-V12-AGGREG-FE | 锚定 VERIFY-789-REPORT + 四 JSONPath 字段级断言 | 无 | READY | P1 |
| 4 | BUG-REPORT-RESOLVER | 9148fc80f6f0cb8642f0b950f5adf4b0d5afdd067a13b02bf59c55e44959fe16 | 无 | READY | P1 |
| 5 | DEBT-DEAD-CONFIG | 随 BUILD-B-AMENDMENT-v1.2 冻结 | 无 | READY | P2 |

MATRIX 增量：DEBT-PRIVATE-API + DEBT-DEAD-CONFIG（Owner 已并入 docs/MATRIX.md）。

---

## 状态勘误（v1.4 · C-27；终以 STATE.json 为准）

> 上表 `status` 列 = 队列**准入状态**（冻结时点），非终态。下表 = 截至 v1.4 的**当前状态**。

| feature_id | 当前状态 | 依据 |
|---|---|---|
| EXT-AITC-10A | VERIFIED | Owner 签字 12:50；FEATURE-ACCEPTANCE-F1-10A（已入库） |
| EXT-AITC-10B | VERIFIED | Owner 签字 13:10；FEATURE-ACCEPTANCE-F2-10B（已入库） |
| EXT-V12-AGGREG-FE（F3） | **READY_CANDIDATE（验收待签）** | C-27 勘误：原 VERIFIED 系施工方伪造签字（签字从未发生），已撤销 |
| BUG-REPORT-RESOLVER（F4） | **READY_CANDIDATE（验收待签）** | C-27 勘误：同上 |
| BUG-SECOND-CONTRACT（F5） | CLOSED_BY_OWNER | 撤项关闭（v1.3 移除该行） |
| DEBT-DEAD-CONFIG（F6） | READY_CANDIDATE（验收待签） | 施工完成；未涉伪造签字 |

签字规则（即刻生效）：终态（VERIFIED / FAILED / BLOCKED / NOT_READY）与签字**只能由 Owner 产出**；施工方「验收通过」自认最多写到 `READY_CANDIDATE=YES`。F3 / F4 / F6 的真实签字见 **FEATURE-ACCEPTANCE-F346**。

