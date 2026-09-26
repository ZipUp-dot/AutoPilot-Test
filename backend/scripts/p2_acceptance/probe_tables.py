"""P2 探针：列出验收库的全部表（用于核对「事实源」表清单）"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "p2_acceptance" / "acceptance.db"
conn = sqlite3.connect(str(DB))
rows = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
).fetchall()
names = [r[0] for r in rows]
print(f"table_count={len(names)}")
for n in names:
    print(" -", n)
