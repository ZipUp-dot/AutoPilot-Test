"""P2 探针：批量生成/执行进度（DB 直查，不依赖日志缓冲）"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "p2_acceptance" / "acceptance.db"


def main():
    if not DB.exists():
        print("DB 不存在")
        return
    conn = sqlite3.connect(str(DB))
    c = conn.cursor()
    for label, sql in [
        ("test_cases", "SELECT COUNT(*) FROM test_cases"),
        ("page_elements", "SELECT COUNT(*) FROM page_elements"),
        ("generated_codes", "SELECT COUNT(*) FROM generated_codes"),
        ("generated_codes_valid", "SELECT COUNT(*) FROM generated_codes WHERE is_valid=1"),
        ("batch_cases", "SELECT COUNT(*) FROM batch_cases"),
        ("batch_records", "SELECT COUNT(*) FROM batch_records"),
        ("executions", "SELECT COUNT(*) FROM executions"),
        ("execution_steps", "SELECT COUNT(*) FROM execution_steps"),
        ("heal_records", "SELECT COUNT(*) FROM heal_records"),
    ]:
        try:
            print(f"{label:24} = {c.execute(sql).fetchone()[0]}")
        except Exception as e:
            print(f"{label:24} ! {e}")
    try:
        rows = c.execute("SELECT status, COUNT(*) FROM batch_cases GROUP BY status").fetchall()
        print("batch_cases by status:", rows)
    except Exception:
        pass
    try:
        rows = c.execute("SELECT status, COUNT(*) FROM execution_steps GROUP BY status").fetchall()
        print("execution_steps by status:", rows)
    except Exception:
        pass
    try:
        rows = c.execute(
            "SELECT case_id, round_no, retry_status, error_type, retry_count FROM heal_records "
            "ORDER BY id LIMIT 20"
        ).fetchall()
        print("heal_records detail (case,round,status,error_type,retry_count):")
        for r in rows:
            print("   ", tuple(r))
    except Exception:
        pass
    try:
        rows = c.execute(
            "SELECT error_type, COUNT(*) FROM execution_steps WHERE status='failed' "
            "GROUP BY error_type"
        ).fetchall()
        print("failed_steps by error_type:", rows)
    except Exception:
        pass
    try:
        rows = c.execute("SELECT retry_status, COUNT(*) FROM heal_records GROUP BY retry_status").fetchall()
        print("heal_records by retry_status:", rows)
    except Exception:
        pass
    try:
        row = c.execute(
            "SELECT COUNT(DISTINCT case_id) FROM execution_steps WHERE status='failed'"
        ).fetchone()
        print("cases_with_failed_step =", row[0] if row else 0)
        row = c.execute(
            "SELECT COUNT(DISTINCT case_id) FROM execution_steps WHERE status='failed' "
            "AND case_id IN (SELECT case_id FROM heal_records)"
        ).fetchone()
        print("failed_cases_healed_or_in_heal =", row[0] if row else 0)
        row = c.execute("SELECT status, COUNT(*) FROM execution_steps GROUP BY status").fetchall()
        print("execution_steps by status:", row)
    except Exception:
        pass
    try:
        row = c.execute("SELECT batch_status, summary_json FROM batch_records ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            import json
            s = json.loads(row[1]) if row[1] else {}
            print("latest batch_record:", row[0],
                  {k: s.get(k) for k in ("requested_count", "kpi_eligible_count",
                                          "first_gen_valid_count", "validation_failed_count",
                                          "deadline_excluded_count", "mock_excluded_count",
                                          "pre_attempt_excluded_count", "success", "failed", "skipped")})
    except Exception:
        pass


if __name__ == "__main__":
    main()
