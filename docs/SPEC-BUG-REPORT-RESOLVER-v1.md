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

---

## 附录 A · 节拍 0 Source Evidence 清单（回填 2026-10-07，Owner 授权）

> 锚点口径：**字段级**（硬边界⑤，不以行号为锚）；行号仅为取证时点参考。
> 冻结内容 SHA256（回填前）= `9148fc80f6f0cb8642f0b950f5adf4b0d5afdd067a13b02bf59c55e44959fe16`
> （与 QUEUE-SNAPSHOT-nb-20261007-B v1.2 声明逐字节一致）；回填后 SHA256 见 F4 验收记录留痕。

| # | 锚点（字段级） | 自判分支事实 | 处置 |
|---|---|---|---|
| P-1 | `ReportService._aggregate` → `final_status`（case 终态兜底 if） | 读 sealed `runtime_state[case_id].case_status` 后**二次解释**：`if final_status not in (success, failed, skipped): final_status = "skipped"`；`entry` 缺失默认 `unknown` 再落 `skipped` | **删除**该兜底 if；case 终态统一由 `CaseStateResolver.resolve([step_to_dict(s) for s in case_steps])` 推出 |
| — 排除 | `ReportService._aggregate` → 截图挑选（`s.status == "failed"`）／`_analyze_errors`／`_top_failed_selectors` | **步骤级**事实筛选，非 case 状态解释 | 不动（超 §1 范围） |
| — 排除 | `report_type_for_status`（Execution 终态分型） | Execution.status → report_type 钉死映射（P0-10） | 不动（AC-02 回归覆盖） |
| — 排除 | `routers/reports.py`、`templates/report_template.html` | 无 case 状态判定（模板对未知值降级渲染） | 不动（非 §14 范围） |

**Resolver 覆盖核验**：`resolve()` 值域 = success/failed/skipped/pending/running/unknown，全量覆盖报告所需 → **无缺场景，不触发 §6 STOP**。

**同范式生产实现（参照）**：`routers/executions.py::_build_case_results`（注释「唯一真源 = CaseStateResolver」）已按 `resolve([step_to_dict(cs) for cs in csteps], context)` 聚合 case 级结果。

**F5 重定位证据（Amendment v1.2 P2-1）**：`SPEC-BUG-SECOND-CONTRACT-v1.md` 指定主文件 `execution_report_service.py` **全仓零命中（不存在）**；其所述「绕开 CaseStateResolver 的状态兜底 if 所在链路」实际落点 = 本表 P-1 → C-21/C-22 疑为同一审计发现的两个切面。
