# FEATURE-SPEC · BUG-REPORT-RESOLVER · Report 状态真源修正

> 状态：**FROZEN**（Owner 2026-10-07 11:05，随梭哈批）· 来源：C-21（TRAE 规则接收审计）

## 1. Definition
Report 生成一律依赖 CaseStateResolver 解释 case 终态，删除/收窄任何"Report 自行重解释
case 状态"的兜底分支。范围限 report_service.py 及其测试。
## 2. Audit
复用 CaseStateResolver（既有唯一状态解释器）；不复用 report 内的自判逻辑（缺陷本体）。
**证据声明**：缺陷位置（report 内自判兜底）来自 TRAE 审计（Derived）；施工节拍 0 必须
先以 Source Evidence 定位全部自判点，清单回填本 Spec 附录后再动手。
## 3. Data Model / 10. Schema
no_schema_change。
## 4. State Model
不引入；只是解释器唯一化。
## 5. Data Flow
report 读取 step/case 事实 → CaseStateResolver → 渲染。无第二解释路径。
## 6. Backend
report_service.py：移除自判兜底，统一调 Resolver；若 Resolver 缺场景 → STOP 上报，
禁止在 report 内补逻辑。
## 7. Frontend
无。
## 8. Integration
§十一口径落地：Report 依赖 CaseStateResolver；Summary 仍为 Derived Data，幂等性不变。
## 9. KPI
无影响（展示口径统一，不改统计定义）。
## 11. AC
AC-01 report 内零自判分支（静态检查：report_service 不得 import/引用 case 状态判定函数，白名单 Resolver 除外）；AC-02 既有报告测试 GREEN（断言不改）；AC-03 边界 case（interrupted/skipped）渲染与 Resolver 输出一致（参数化单测）。
## 12. Tests
test_report_resolver_only.py 新增（RED 先行）；既有 tests/services/test_report* 回归。
## 13. Risk
输出变化风险：若自判兜底曾"修正"Resolver 结果，统一后报告展示会变——施工时逐场景 diff，
变化清单随验收记录上报 Owner。
## 14. Files
report_service.py → 测试 → commit fix(report): 状态解释统一走 CaseStateResolver（BUG-REPORT-RESOLVER）
