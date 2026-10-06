"""heal 诊断脚本（只读）— P0 诊断基建

职责（严格遵守冻结约束）：
  1. 正式指标采集入口：复用 MetricsService.overview()（禁止另起口径）。
  2. 失败分析：只读【真实存在】字段——
       - root ExecutionStep.error_type（路由层）
       - HealRecord.error_type（Round 终态层）
       - attempt 层 validator_result / validator_error / rerun_result /
         rerun_error_type / candidate_code
     【禁止读取不存在的字段，禁止新增 error_type 值】。
  3. 诊断分类仅限证据可证类别（DIAGNOSIS_CATEGORIES 八枚）；
     推断性标签（如 candidate_wrong）只进报告并显式标注 "analysis_inference"，
     【绝不写入任何平台字段 / 数据库】。
  4. 输出 reports/heal_diagnosis.md。

attempts 双 schema（GROUND_TRUTH 3c 实锤）：
  - 写入方 A（旧逐 step）：generated_code / status / error
  - 写入方 B（Case 级 Round）：candidate_code / validator_result / rerun_result /
    rerun_error_type / validator_error / rerun_message / error_message
  两者结构不同，禁止统一假设；本脚本按条目自适应解析（parse_attempts）。

只读保证：本脚本只执行 SELECT（ORM query）；唯一写操作是导出 md 报告文件，
不触碰 Execution / ExecutionStep / HealRecord / GeneratedCode 任何数据行。

用法：
  python scripts/heal_diagnose.py [--execution-id 12] [--case-id 34] [--out reports/heal_diagnosis.md]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 允许从 backend/ 根目录直接执行
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── 诊断分类：证据可证类别（闭集，禁止扩张）──
DIAGNOSIS_CATEGORIES = (
    "deadline_timeout",
    "validator_failed",
    "candidate_rerun_failed",
    "ai_request_failed",
    "ai_schema_failed",
    "infrastructure_failed",
    "cancelled",
    "other",
)

# Round 终态 error_type（冻结七值）→ 诊断分类（证据可证映射）
ERROR_TYPE_TO_CATEGORY = {
    "deadline_exceeded": "deadline_timeout",
    "ai_request_failed": "ai_request_failed",
    "ai_schema_error": "ai_schema_failed",
    "validation_error": "validator_failed",
    "heal_exhausted": "candidate_rerun_failed",
    "worker_failed": "infrastructure_failed",
    "heal_finalization_error": "infrastructure_failed",
}

# 推断性标签（只进报告，标注 analysis_inference，绝不入平台字段）
INFERENCE_LABELS = ("candidate_wrong", "selector_drift_suspected")


def parse_attempt(entry: dict) -> dict:
    """解析单条 attempt，标注来源 schema（A / B）。

    A（旧逐 step 写入方）：generated_code / status / error
    B（Case 级 Round 写入方）：candidate_code / validator_result / rerun_result /
      rerun_error_type / validator_error / rerun_message / error_message
    禁止统一假设；无判别键 → schema="unknown"（保守，不猜）。
    """
    if not isinstance(entry, dict):
        return {"_schema": "unknown", "_raw": entry}
    out = dict(entry)
    if "validator_result" in entry or "rerun_result" in entry or "candidate_code" in entry:
        out["_schema"] = "B"
    elif "generated_code" in entry or "status" in entry or "error" in entry:
        out["_schema"] = "A"
    else:
        out["_schema"] = "unknown"
    return out


def parse_attempts(attempts_json: Optional[str]) -> list[dict]:
    """解析 HealRecord.attempts（Text，JSON 字符串）→ 逐条标注 schema 的列表"""
    if not attempts_json:
        return []
    try:
        data = json.loads(attempts_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [parse_attempt(e) for e in data]


def classify_failure(record: Any, attempts: list[dict]) -> Optional[str]:
    """按【证据可证】顺序给出诊断分类；Round 成功（非失败）返回 None。

    证据优先级：retry_status → HealRecord.error_type → attempt 层 rerun/validator 事实。
    仅返回 DIAGNOSIS_CATEGORIES 内取值，禁止新增。
    """
    retry_status = getattr(record, "retry_status", None)
    if retry_status == "success":
        return None
    if retry_status == "cancelled_by_recovery":
        return "cancelled"
    if retry_status in ("pending", "retrying", "finalizing"):
        return "other"  # 非终态（理论上不应出现在诊断样本）

    error_type = getattr(record, "error_type", None)
    if error_type in ERROR_TYPE_TO_CATEGORY:
        return ERROR_TYPE_TO_CATEGORY[error_type]

    # 回退：attempt 层真实字段（仅 B schema 具备 rerun/validator 事实）
    rerun_types = [a.get("rerun_error_type") for a in attempts if a.get("_schema") == "B"]
    if "deadline_exceeded" in rerun_types:
        return "deadline_timeout"
    if "worker_failed" in rerun_types:
        return "infrastructure_failed"
    if "ai_request_failed" in rerun_types:
        return "ai_request_failed"
    if any(a.get("validator_result") is False for a in attempts):
        return "validator_failed"
    if any(a.get("rerun_result") == "failed" for a in attempts):
        return "candidate_rerun_failed"
    return "other"


def collect_inferences(record: Any, attempts: list[dict]) -> list[str]:
    """推断性标签（analysis_inference）——只进报告文本，绝不写库 / 不写平台字段。"""
    inferred: list[str] = []
    category = classify_failure(record, attempts)
    if category == "candidate_rerun_failed":
        # 有 valid candidate 被实际 rerun 且业务断言失败 → 推测「候选语义不正确」
        if any(a.get("rerun_result") == "failed" for a in attempts):
            inferred.append("candidate_wrong")
    return [f"{lbl} (analysis_inference)" for lbl in inferred]


def _root_step_error_type(db, record) -> Optional[str]:
    """root ExecutionStep.error_type（路由层）；缺 root 时回退 execution_step_id。"""
    from app.models.execution_step import ExecutionStep

    step_id = getattr(record, "root_execution_step_id", None) or getattr(record, "execution_step_id", None)
    if not step_id:
        return None
    row = db.query(ExecutionStep.error_type).filter(ExecutionStep.id == step_id).first()
    return row[0] if row else None


def diagnose(db, execution_id: Optional[int] = None, case_id: Optional[int] = None) -> dict:
    """只读诊断聚合（不写任何业务数据）。"""
    from app.models.heal_record import HealRecord
    from app.services.metrics_service import MetricsService

    q = db.query(HealRecord)
    if execution_id is not None:
        q = q.filter(HealRecord.execution_id == execution_id)
    if case_id is not None:
        q = q.filter(HealRecord.case_id == case_id)

    records: list[dict] = []
    category_counts: dict[str, int] = {c: 0 for c in DIAGNOSIS_CATEGORIES}
    schema_counts = {"A": 0, "B": 0, "unknown": 0}
    other_breakdown: dict[str, int] = {}

    for rec in q.all():
        attempts = parse_attempts(rec.attempts)
        for a in attempts:
            schema_counts[a["_schema"]] = schema_counts.get(a["_schema"], 0) + 1

        outcome = "success" if rec.retry_status == "success" else (
            "cancelled" if rec.retry_status == "cancelled_by_recovery" else "failed"
        )
        category = classify_failure(rec, attempts)
        if category is not None:
            category_counts[category] = category_counts.get(category, 0) + 1
        if category == "other":
            # 证据可证的 other 归因（仅真实字段拼接，不新增枚举值）
            key = f"retry_status={rec.retry_status}/error_type={rec.error_type}"
            other_breakdown[key] = other_breakdown.get(key, 0) + 1

        records.append({
            "heal_record_id": rec.id,
            "execution_id": rec.execution_id,
            "case_id": rec.case_id,
            "round_no": rec.round_no,
            "outcome": outcome,
            "retry_status": rec.retry_status,
            "heal_error_type": rec.error_type,                       # Round 终态层
            "root_step_error_type": _root_step_error_type(db, rec),  # 路由层
            "attempts_schema": sorted({a["_schema"] for a in attempts}),
            "attempts": attempts,
            "diagnosis_category": category,      # 闭集；success → None
            "analysis_inference": collect_inferences(rec, attempts),
        })

    # 正式指标采集：复用 MetricsService.overview()（禁止另起口径）
    formal_kpi = MetricsService(db).overview(project_id=None)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "filter": {"execution_id": execution_id, "case_id": case_id},
        "heal_records_total": len(records),
        "attempts_schema_counts": schema_counts,
        "diagnosis_category_counts": category_counts,
        "other_breakdown": other_breakdown,
        "formal_kpi": formal_kpi,
        "records": records,
    }


# ── 报告渲染 ──

def render_markdown(result: dict) -> str:
    lines: list[str] = []
    ap = lines.append

    ap("# heal 诊断报告（只读）")
    ap("")
    ap(f"- 生成时间（UTC）：`{result['generated_at']}`")
    ap(f"- 过滤条件：`{result['filter']}`")
    ap(f"- HealRecord 样本数：**{result['heal_records_total']}**")
    ap("- 本报告由 `backend/scripts/heal_diagnose.py` 只读生成；`analysis_inference` 标签仅存在于报告文本，"
       "不写入任何平台字段/数据库。")
    ap("")

    ap("## 1. 正式指标采集入口")
    ap("")
    ap("> 复用 `MetricsService.overview()`（口径与线上一致，禁止另起口径）。")
    ap("> 诚实标注：`metrics_service` 仅暴露 4 项正式 KPI；“六项”未在仓库中定义，未擅自发明。")
    ap("")
    ap("```json")
    ap(json.dumps(result["formal_kpi"], ensure_ascii=False, indent=2))
    ap("```")
    ap("")

    ap("## 2. 诊断分类分布（证据可证，闭集）")
    ap("")
    ap("| 分类 | 计数 |")
    ap("|---|---|")
    for k in DIAGNOSIS_CATEGORIES:
        ap(f"| {k} | {result['diagnosis_category_counts'].get(k, 0)} |")
    ap("")
    ap(f"- attempts schema 计数（A=旧逐 step / B=Case 级 Round）：`{result['attempts_schema_counts']}`")
    ap("")
    if result.get("other_breakdown"):
        ap("`other` 的证据归因（真实字段拼接，未新增枚举值）：")
        ap("")
        ap("| retry_status / error_type | 计数 |")
        ap("|---|---|")
        for k, v in sorted(result["other_breakdown"].items(), key=lambda kv: -kv[1]):
            ap(f"| {k} | {v} |")
        ap("")

    ap("## 3. 逐条 HealRecord 分析")
    ap("")
    if not result["records"]:
        ap("_无匹配样本_")
        ap("")
    for r in result["records"]:
        ap(f"### HealRecord #{r['heal_record_id']}（execution={r['execution_id']} / case={r['case_id']} / round={r['round_no']}）")
        ap("")
        ap(f"- outcome：`{r['outcome']}`（retry_status=`{r['retry_status']}`）")
        ap(f"- heal_error_type（Round 终态层）：`{r['heal_error_type']}`")
        ap(f"- root_step_error_type（路由层）：`{r['root_step_error_type']}`")
        ap(f"- diagnosis_category：`{r['diagnosis_category']}`")
        if r["analysis_inference"]:
            ap(f"- analysis_inference（仅报告，绝不入库）：`{r['analysis_inference']}`")
        ap("")
        if r["attempts"]:
            ap("| attempt | schema | validator_result | validator_error | rerun_result | rerun_error_type |")
            ap("|---|---|---|---|---|---|")
            for a in r["attempts"]:
                ap("| {att} | {sch} | {vr} | {ve} | {rr} | {ret} |".format(
                    att=a.get("attempt"),
                    sch=a.get("_schema"),
                    vr=a.get("validator_result"),
                    ve=(str(a.get("validator_error"))[:60] if a.get("validator_error") else ""),
                    rr=a.get("rerun_result"),
                    ret=a.get("rerun_error_type"),
                ))
            ap("")
        else:
            ap("_无 attempt 记录_")
            ap("")

    ap("## 4. Formal KPI 排除判定（Replay 是否进入 85% / Coverage）")
    ap("")
    ap("**判定：现有谓词无法天然排除 Replay Execution → 该子任务 STOP & REPORT。**")
    ap("")
    ap("- 85% 分母谓词：`execution.status ∈ TERMINAL_STATUSES` 且该 case 的 "
       "`runtime_state[case_id].terminal_reason` 非空，排除 {user_stopped, interrupted}。"
       "证据位置：`app/services/metrics_service.py:78-91`。")
    ap("- `terminal_reason` 由 Seal 统一写入：`app/services/execution_finalizer.py:288-307`"
       "（`_persist_runtime_state`）。任何走到 Seal 的 Execution（含 Replay）都会获得该字段。")
    ap("- 无任何 “admitted production / diagnostic” 区分标记；Admission 是唯一入口且不区分用途"
       "（`app/services/execution_admission_service.py:67-190`）。")
    ap("- 因此 Replay 产生的 Execution **会被计入** 85% 与 Coverage 分母。按约束，"
       "**禁止发明任何排除字段/标记**，故本子任务停止并上报。")
    ap("")

    ap("## 5. Diagnostic Replay 状态（STOP & REPORT）")
    ap("")
    ap("**结论：现有 Admission 无法安全表达 Replay，禁止发明 `is_diagnostic` 字段。**")
    ap("")
    ap("Replay 要求：以原 Execution 的 Manifest `original_code_id` 构造新 Execution，"
       "禁止读取 Project latest code。现有 Admission 三个代码来源入口均不满足：")
    ap("")
    ap("| 入口 | 代码来源 | 是否满足 |")
    ap("|---|---|---|")
    ap("| `batch_id` | BatchCase.code_id（需批次集合完全一致） | 否（与 Replay 语义无关） |")
    ap("| `retry_from_execution_id` | 源 Execution `runtime_state.active_code_id` | 否（非 Manifest `original_code_id`；"
       "heal 成功后该值指向 healed code，与原始失败代码不同） |")
    ap("| 默认（manual/execute-only） | `get_effective_code(...)` = 当前有效代码（≈Project latest） | 否（明令禁止） |")
    ap("")
    ap("- 证据位置：`app/services/execution_admission_service.py:288-338`（`_resolve_and_freeze`）、"
       "`:387-414`（`_retry_candidates` 读 `runtime_state[...].active_code_id`）、"
       "`:309-322`（默认入口 `get_effective_code`）。")
    ap("- 另：`_verify_code`（`:416-435`）要求 `code.source_steps_hash == 当前 TestCase hash` 且过当前 "
       "Validator；回放旧代码极易被合法拒绝（约束⑤），亦属“无法安全表达”。")
    ap("- 结论：不实现 Replay，不新增字段，不绕过任何 Admission 校验。等待规格补充可安全表达 Replay 的入口。")
    ap("")

    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="heal 只读诊断")
    parser.add_argument("--execution-id", type=int, default=None)
    parser.add_argument("--case-id", type=int, default=None)
    parser.add_argument(
        "--out", default=str(_ROOT / "reports" / "heal_diagnosis.md"),
    )
    args = parser.parse_args(argv)

    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        result = diagnose(db, execution_id=args.execution_id, case_id=args.case_id)
    finally:
        db.close()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(result), encoding="utf-8")

    print("=" * 72)
    print(f"HealRecord 样本 = {result['heal_records_total']}")
    print(f"分类分布 = {result['diagnosis_category_counts']}")
    print(f"attempts schema = {result['attempts_schema_counts']}")
    print("判定：Replay 与 Formal KPI 排除均 STOP & REPORT（见报告第 4/5 节）")
    print("=" * 72)
    print(f"detail -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
