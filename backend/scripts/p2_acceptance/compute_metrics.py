"""P2 验收测量（三）：从验收库计算四项立项指标（只读，Case 级聚合）

口径（钉死，禁止批次平均、禁止只统计成功 Case）：

  ① 70% 首生成有效率（工程代理指标）
       = SUM(batch_records.summary_json.first_gen_valid_count)
         / SUM(batch_records.summary_json.kpi_eligible_count)

  ② 85% 最终成功率（Case 级，与 BatchCase.kpi_eligible 完全无关）
       分母 = 终态 Execution 的 runtime_state 中存在 terminal_reason 的 Case，
              且 terminal_reason ∉ {user_stopped, interrupted}
              （business_failure / execution_failed / integrity_anomaly 保留在分母）
       分子 = 分母中 terminal_reason == normal_success 的 Case

  ③ Excel 批量 ≥100 行：端到端（导入 total/success + 入库 TestCase 数 + BatchCase 数）

  ④ 60s：E2E Case latency（per-case = SUM(execution_steps.duration_ms)）的 P50/P95，
       分 E2E / first-pass / healed-path 三组分别统计；
       first-pass = 未创建 HealRecord 即 terminal 的 Case；
       healed path = 至少存在一条 HealRecord 的 Case；
       latency 样本【包含失败 terminal Case】（防幸存者偏差）。

用法：python scripts/p2_acceptance/compute_metrics.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "p2_acceptance" / "acceptance.db"
STATE = ROOT / "data" / "p2_acceptance" / "pipeline_state.json"
OUT = ROOT / "data" / "p2_acceptance" / "metrics.json"

EXCLUDE_REASONS = {"user_stopped", "interrupted"}


def pct(values, q):
    """线性插值分位（numpy 风格），values 为已排序列表"""
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return float(values[lo] + (values[hi] - values[lo]) * frac)


def dist(values):
    vals = sorted(v for v in values if v is not None)
    return {
        "n": len(vals),
        "p50_ms": pct(vals, 0.50),
        "p95_ms": pct(vals, 0.95),
        "min_ms": vals[0] if vals else None,
        "max_ms": vals[-1] if vals else None,
        "mean_ms": round(sum(vals) / len(vals), 1) if vals else None,
    }


def main():
    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 1
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    out = {}

    # ── ① 70% 首生成有效率 ──
    total_valid = total_eligible = 0
    batch_rows = []
    for r in cur.execute("SELECT batch_id, batch_status, summary_json FROM batch_records"):
        s = json.loads(r["summary_json"]) if r["summary_json"] else {}
        v = int(s.get("first_gen_valid_count") or 0)
        e = int(s.get("kpi_eligible_count") or 0)
        total_valid += v
        total_eligible += e
        batch_rows.append({
            "batch_id": r["batch_id"], "batch_status": r["batch_status"],
            "requested_count": s.get("requested_count"),
            "kpi_eligible_count": e, "first_gen_valid_count": v,
            "validation_failed_count": s.get("validation_failed_count"),
            "deadline_excluded_count": s.get("deadline_excluded_count"),
            "mock_excluded_count": s.get("mock_excluded_count"),
            "pre_attempt_excluded_count": s.get("pre_attempt_excluded_count"),
            "success": s.get("success"), "failed": s.get("failed"), "skipped": s.get("skipped"),
        })
    out["metric_70_first_gen_valid_rate"] = {
        "formula": "SUM(first_gen_valid_count)/SUM(kpi_eligible_count)",
        "numerator": total_valid,
        "denominator": total_eligible,
        "rate": (total_valid / total_eligible) if total_eligible else None,
        "batches": batch_rows,
    }

    # ── ② 85% 最终成功率（Case 级）──
    ratio_num = ratio_den = 0
    reason_counts: dict[str, int] = {}
    exec_rows = []
    terminal_cases = []  # (execution_id, case_id, reason, case_status)
    for ex in cur.execute(
        "SELECT id, status, total_cases, start_time, end_time, runtime_state_json "
        "FROM executions ORDER BY id"
    ):
        runtime = json.loads(ex["runtime_state_json"]) if ex["runtime_state_json"] else {}
        ex_den = ex_num = 0
        for cid, entry in runtime.items():
            reason = (entry or {}).get("terminal_reason")
            if not reason:
                continue
            terminal_cases.append((ex["id"], int(cid), reason,
                                   (entry or {}).get("case_status")))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if reason in EXCLUDE_REASONS:
                continue
            ex_den += 1
            if reason == "normal_success":
                ex_num += 1
        ratio_num += ex_num
        ratio_den += ex_den
        exec_rows.append({
            "execution_id": ex["id"], "status": ex["status"],
            "total_cases": ex["total_cases"],
            "cases_with_terminal_reason": len(runtime),
            "denominator": ex_den, "numerator": ex_num,
            "start_time": ex["start_time"], "end_time": ex["end_time"],
        })
    out["metric_85_final_success_rate"] = {
        "formula": ("SUM(terminal_reason==normal_success) / "
                    "SUM(admitted production Case, terminal_reason∉{user_stopped,interrupted})"),
        "numerator": ratio_num,
        "denominator": ratio_den,
        "rate": (ratio_num / ratio_den) if ratio_den else None,
        "reason_breakdown": reason_counts,
        "executions": exec_rows,
    }

    # ── ③ Excel ≥100 行端到端 ──
    import_state = {}
    if STATE.exists():
        import_state = json.loads(STATE.read_text(encoding="utf-8")).get("import_result") or {}
    def one(sql):
        row = cur.execute(sql).fetchone()
        return row[0] if row else 0
    out["metric_excel_bulk_import"] = {
        "import_response": import_state,
        "test_cases_in_db": one("SELECT COUNT(*) FROM test_cases"),
        "batch_cases_in_db": one("SELECT COUNT(*) FROM batch_cases"),
        "generated_codes_in_db": one("SELECT COUNT(*) FROM generated_codes"),
        "page_elements_in_db": one("SELECT COUNT(*) FROM page_elements"),
        "execution_steps_in_db": one("SELECT COUNT(*) FROM execution_steps"),
    }

    # ── ④ latency（E2E / first-pass / healed），含失败 terminal Case ──
    heal_case_keys = set()
    heal_rows = []
    for h in cur.execute("SELECT execution_id, case_id, round_no, retry_status, error_type FROM heal_records"):
        heal_case_keys.add((h["execution_id"], h["case_id"]))
        heal_rows.append(dict(h))
    out["heal_records"] = {
        "count": len(heal_rows),
        "cases_claimed": len(heal_case_keys),
        "rows": heal_rows,
    }

    step_sum: dict[tuple, int] = {}
    for r in cur.execute(
        "SELECT execution_id, case_id, SUM(duration_ms) AS d FROM execution_steps "
        "GROUP BY execution_id, case_id"
    ):
        if r["d"] is not None:
            step_sum[(r["execution_id"], r["case_id"])] = int(r["d"])

    e2e, first_pass, healed = [], [], []
    per_case = []
    for (eid, cid, reason, cstatus) in terminal_cases:
        lat = step_sum.get((eid, cid))
        is_healed = (eid, cid) in heal_case_keys
        rec = {
            "execution_id": eid, "case_id": cid, "terminal_reason": reason,
            "case_status": cstatus, "latency_ms": lat,
            "path": "healed" if is_healed else "first-pass",
        }
        per_case.append(rec)
        if lat is not None:
            e2e.append(lat)
            (healed if is_healed else first_pass).append(lat)

    failed_incl = [r for r in per_case if r["terminal_reason"] != "normal_success"]
    out["metric_60s_latency"] = {
        "definition": "per-case latency = SUM(execution_steps.duration_ms)",
        "cohort_definition": {
            "first-pass": "未创建 HealRecord 即 terminal 的 Case",
            "healed-path": "至少存在一条 HealRecord 的 Case",
        },
        "survivorship": {
            "samples_include_failed_terminal_cases": True,
            "failed_terminal_cases_in_sample": len(failed_incl),
            "failed_terminal_cases_with_latency": len([r for r in failed_incl if r["latency_ms"] is not None]),
        },
        "e2e": dist(e2e),
        "first_pass": dist(first_pass),
        "healed_path": dist(healed),
        "e2e_p95_le_60s": (dist(e2e)["p95_ms"] is not None and dist(e2e)["p95_ms"] <= 60000),
        "per_case": per_case,
    }

    # ── 上下文：生成侧延迟分布 + wall-clock（用于差距归因，不参与四项指标口径）──
    gen_by_key: dict[str, list[int]] = {}
    for r in cur.execute("SELECT status, error_type, latency_ms FROM batch_cases"):
        key = r["error_type"] or r["status"]
        gen_by_key.setdefault(key, []).append(int(r["latency_ms"] or 0))
    out["context_generation_latency"] = {k: dist(v) for k, v in sorted(gen_by_key.items())}

    exec_row = cur.execute(
        "SELECT start_time, end_time, status FROM executions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    wall = None
    if exec_row and exec_row["start_time"] and exec_row["end_time"]:
        from datetime import datetime as _dt
        fmt = "%Y-%m-%d %H:%M:%S.%f"
        t0 = _dt.strptime(exec_row["start_time"], fmt)
        t1 = _dt.strptime(exec_row["end_time"], fmt)
        wall = {
            "status": exec_row["status"],
            "start": exec_row["start_time"], "end": exec_row["end_time"],
            "wall_seconds": round((t1 - t0).total_seconds(), 1),
        }
        if terminal_cases:
            wall["admitted_cases"] = len(terminal_cases)
            wall["wall_seconds_per_case"] = round(
                (t1 - t0).total_seconds() / len(terminal_cases), 1)
    out["context_execution_wall_clock"] = wall
    # batch terminal_at 取 summary 原值（生成阶段 wall-clock 终点）
    for r in cur.execute("SELECT summary_json FROM batch_records ORDER BY id DESC LIMIT 1"):
        s = json.loads(r["summary_json"]) if r["summary_json"] else {}
        out["context_batch_terminal_at"] = s.get("terminal_at")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 控制台摘要 ──
    m70 = out["metric_70_first_gen_valid_rate"]
    m85 = out["metric_85_final_success_rate"]
    lat = out["metric_60s_latency"]
    exc = out["metric_excel_bulk_import"]
    print("=" * 72)
    print(f"① 70% 首生成有效率 = {m70['numerator']}/{m70['denominator']} = "
          f"{m70['rate'] if m70['rate'] is None else round(m70['rate'], 4)}")
    print(f"② 85% 最终成功率   = {m85['numerator']}/{m85['denominator']} = "
          f"{m85['rate'] if m85['rate'] is None else round(m85['rate'], 4)}")
    print(f"   terminal_reason 分布 = {m85['reason_breakdown']}")
    print(f"③ Excel 导入 total/success = {exc['import_response'].get('total')}/"
          f"{exc['import_response'].get('success')}；TestCase={exc['test_cases_in_db']} "
          f"BatchCase={exc['batch_cases_in_db']} GeneratedCode={exc['generated_codes_in_db']}")
    print(f"④ E2E   n={lat['e2e']['n']} P50={lat['e2e']['p50_ms']} P95={lat['e2e']['p95_ms']}")
    print(f"   first n={lat['first_pass']['n']} P50={lat['first_pass']['p50_ms']} P95={lat['first_pass']['p95_ms']}")
    print(f"   healed n={lat['healed_path']['n']} P50={lat['healed_path']['p50_ms']} P95={lat['healed_path']['p95_ms']}")
    print(f"   P95<=60s ? {lat['e2e_p95_le_60s']}；失败 terminal Case 样本 = "
          f"{lat['survivorship']['failed_terminal_cases_with_latency']}")
    print("=" * 72)
    print(f"detail -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
