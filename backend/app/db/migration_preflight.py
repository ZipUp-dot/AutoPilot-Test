"""迁移预检 — Alembic 0002 执行前扫描孤儿行与 UNIQUE 冲突

P0-11：迁移失败显式抛错退出，禁止静默吞错。本模块在 0002_formal_delta.upgrade()
开头被调用；发现问题则返回清单（调用方拒绝完成迁移）。

检查项：
  1. 孤儿行：历史事实链 FK 将改为 RESTRICT / 新增 RESTRICT FK，
     若存在 FK 值无对应父行，删除父对象将被 DB 阻断或产生脏数据 → 拒绝。
  2. UNIQUE 冲突：execution_steps(execution_id,case_id,step_index)、
     heal_records(execution_id,case_id,round_no)、
     execution_reports(execution_id,report_type) 将新增 UNIQUE，
     已存在的重复行会使约束创建失败 → 拒绝。

NULL 处理：SQLite / MySQL 的 UNIQUE 均允许多个 NULL（NULL≠NULL），
故 heal_records 的 UNIQUE 冲突检查只统计非 NULL 三列均重复的行。
"""

from __future__ import annotations

import sqlalchemy as sa

# (表, 子列, 父表) —— 需要检查孤儿行的 FK 边（历史事实链，0002 后均为 RESTRICT）
_FK_EDGES: list[tuple[str, str, str]] = [
    ("generated_codes", "case_id", "test_cases"),
    ("executions", "project_id", "projects"),
    ("execution_steps", "execution_id", "executions"),
    ("execution_steps", "case_id", "test_cases"),
    ("execution_reports", "execution_id", "executions"),
    ("heal_records", "execution_step_id", "execution_steps"),
    ("heal_records", "execution_id", "executions"),
    ("heal_records", "case_id", "test_cases"),
    ("heal_records", "original_code_id", "generated_codes"),
    ("heal_records", "healed_code_id", "generated_codes"),
    ("heal_records", "root_execution_step_id", "execution_steps"),
]

# (表, 列组, 约束名) —— 新增的 UNIQUE 约束
_UNIQUE_CHECKS: list[tuple[str, list[str], str]] = [
    ("execution_steps", ["execution_id", "case_id", "step_index"],
     "uq_execution_steps_execution_id_case_id_step_index"),
    ("heal_records", ["execution_id", "case_id", "round_no"],
     "uq_heal_records_execution_id_case_id_round_no"),
    ("execution_reports", ["execution_id", "report_type"],
     "uq_execution_reports_execution_id_report_type"),
]

_ORPHAN_SQL = (
    "SELECT COUNT(*) FROM {child} c WHERE c.{col} IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.id = c.{col})"
)


def run_preflight(conn) -> list[str]:
    """扫描给定连接对应的数据库，返回问题清单（空列表 = 通过）。"""
    issues: list[str] = []
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    # 1) 孤儿行（跳过列尚不存在的边 —— 预检运行在 0002 加列之前）
    for child, col, parent in _FK_EDGES:
        if child not in tables or parent not in tables:
            continue
        child_cols = {c["name"] for c in inspector.get_columns(child)}
        if col not in child_cols:
            continue
        cnt = conn.execute(sa.text(_ORPHAN_SQL.format(child=child, col=col, parent=parent))).scalar()
        if cnt:
            sample_ids = conn.execute(
                sa.text(
                    "SELECT c.id FROM {child} c WHERE c.{col} IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.id = c.{col}) "
                    "LIMIT 5".format(child=child, col=col, parent=parent)
                )
            ).scalars().all()
            issues.append(
                "孤儿行: {child}.{col} → {parent} 存在 {cnt} 条无父引用"
                "{samples}".format(
                    child=child, col=col, parent=parent, cnt=cnt,
                    samples="，样例 id=%s" % (sample_ids,) if sample_ids else "",
                )
            )

    # 2) UNIQUE 冲突（跳过列尚不存在的检查）
    for table, cols, constraint in _UNIQUE_CHECKS:
        if table not in tables:
            continue
        table_cols = {c["name"] for c in inspector.get_columns(table)}
        if not set(cols).issubset(table_cols):
            continue
        col_expr = ", ".join(cols)
        where = " AND ".join("c.%s IS NOT NULL" % c for c in cols)
        rows = conn.execute(
            sa.text(
                "SELECT {cols} FROM {table} c WHERE {where} "
                "GROUP BY {cols} HAVING COUNT(*) > 1".format(
                    cols=col_expr, table=table, where=where
                )
            )
        ).fetchall()
        if rows:
            issues.append(
                "UNIQUE 冲突: {table} 上即将新增约束 {constraint}，"
                "已存在 {n} 组重复: {first}".format(
                    table=table, constraint=constraint, n=len(rows),
                    first="; ".join(str(r) for r in rows[:3]),
                )
            )

    return issues
