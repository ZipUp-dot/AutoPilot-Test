"""P2 探针：Execution 终态与 runtime_state 摘要"""
import json
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "p2_acceptance" / "acceptance.db"
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
for ex in conn.execute("SELECT * FROM executions ORDER BY id"):
    print(f"execution_id={ex['id']} status={ex['status']} total_cases={ex['total_cases']} "
          f"passed={ex['passed_cases']} failed={ex['failed_cases']} progress={ex['progress']}")
    print(f"  start={ex['start_time']} end={ex['end_time']} stop_requested_at={ex['stop_requested_at']}")
    rt = json.loads(ex["runtime_state_json"]) if ex["runtime_state_json"] else {}
    reasons = Counter((v or {}).get("terminal_reason") for v in rt.values())
    print(f"  runtime_cases={len(rt)} terminal_reason_dist={dict(reasons)}")
    cs = Counter((v or {}).get("case_status") for v in rt.values())
    print(f"  case_status_dist={dict(cs)}")
