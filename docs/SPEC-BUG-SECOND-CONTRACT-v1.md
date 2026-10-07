# FEATURE-SPEC · BUG-SECOND-CONTRACT · 第二 Execution Contract 处置

> 状态：**FROZEN**（Owner 2026-10-07 11:05，随梭哈批）· 来源：C-22（TRAE 规则接收审计）

## 1. Definition
处置 execution_report_service.py 中被发现的第二 Execution 入口/Contract（绕开
CaseStateResolver 的状态兜底 if 所在链路），确保 Execution 入口唯一（orchestrator/
execution_state 体系）。
**证据声明**：缺陷详情来自 TRAE 审计（Derived）；施工节拍 0 以 Source Evidence 定位
该入口的调用方与可达性，确认是死代码（删）还是活路径（改挂正式入口），结论回填附录。
## 2. Audit
唯一 Execution Contract = orchestrator + execution_state；entry_service 为执行入口面。
第二 Contract 处置方式二选一（Owner 授权施工方按证据选：死代码→删；活路径→改挂）。
## 3/10. Data/Schema
no_schema_change。
## 4. State
不引入。
## 5. Data Flow
调用方 → 唯一 Execution 入口；无旁路。
## 6. Backend
execution_report_service.py（+必要时的调用方）；禁改 Execution 核心状态机。
## 7. Frontend 无。8. Integration：7.6「绕正式入口」禁令落地。9. KPI 无。
## 11. AC
AC-01 全仓静态扫描仅剩一个 Execution Contract 入口族；AC-02 既有执行/报告测试全量 GREEN；
AC-03 若删代码：grep 零调用方（证据）；若改挂：走正式入口的集成测试。
## 12. Tests
新增/调整（RED 先行）；回归全量。
## 13. Risk
误删活路径 → 由 AC-03 证据门槛拦截；处置结论若超出二选一 → STOP 上报 Owner。
## 14. Files
execution_report_service.py（主）→ 可能 1-2 个调用方 → commit fix(exec): 处置第二 Execution Contract（BUG-SECOND-CONTRACT）
