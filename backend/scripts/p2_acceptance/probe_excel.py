"""P2 探针：核对 ExcelParser 对 cases_120.xlsx 的解析结果（列名/步骤/action 枚举）"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.utils.excel_parser import ExcelParser  # noqa: E402

EXCEL = ROOT / "data" / "p2_acceptance" / "cases_120.xlsx"


def main():
    content = EXCEL.read_bytes()
    result = ExcelParser.parse(content, EXCEL.name)
    print(f"total_rows={result.total_rows} success={result.success} failed={result.failed}")
    if result.errors:
        print("errors:", result.errors[:5])
    actions = Counter()
    for c in result.cases:
        for s in c.steps:
            actions[s.action] += 1
    print("action_enum =", dict(actions))
    first = result.cases[0]
    print(f"first_case: no={first.case_no} name={first.case_name} "
          f"priority={first.priority} steps={len(first.steps)}")
    for s in first.steps:
        print(f"  step {s.action:<14} target={s.target[:55]!r} value={s.value[:30]!r}")
    nav = [s.target for c in result.cases for s in c.steps if s.action == "navigate"]
    print("sample_navigate_targets =", nav[:3])


if __name__ == "__main__":
    main()
