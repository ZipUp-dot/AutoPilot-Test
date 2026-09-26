"""Legacy Baseline — 旧库（无 alembic_version）升级锚点（P0-11）

Legacy Bridge 纪律：
  - stamp 只写版本表、不执行任何 migration；【禁止 stamp head 后再 upgrade head】
    （确定性矛盾：stamp head 会把库标成 head，upgrade 变空操作）。
  - Legacy Baseline 必须是『完整 Legacy Schema』的锚点：
    backend/alembic/versions/0001_initial_schema.py 即该锚点
    （LEGACY_BASELINE_REVISION = 0001_initial_schema）。
  - Compatibility Check：逐表逐列逐约束比对，不兼容 → 拒绝并打印差异清单，
    禁止把不完整旧库假装成 baseline（no-op baseline + delta-only revision
    会让空库只得到增量、得不到基础表，是确定缺陷）。
  - 本模块不做任何结构变更；结构统一由 Alembic 定义。
"""

from __future__ import annotations

import sqlalchemy as sa

LEGACY_BASELINE_REVISION = "0001_initial_schema"

# 完整 Legacy Schema 权威规格（与 alembic/versions/0001_initial_schema.py 一致）
# 列类型以 SQLite 反射归一化形式给出；比对时两侧统一归一到「大类型」桶
# （字符串族 varchar / text / json / longtext 等视为等价，见 _STRING_FAMILY）
_LEGACY_SCHEMA: dict[str, dict] = {
    "projects": {
        "columns": {
            "id": "integer", "name": "varchar", "target_url": "varchar",
            "test_path": "varchar", "browser_type": "varchar", "headless": "integer",
            "status": "varchar", "platform": "varchar", "config_json": "text",
            "created_at": "datetime", "updated_at": "datetime",
        },
        "fks": [],
        "uniques": [],
    },
    "page_elements": {
        "columns": {
            "id": "integer", "project_id": "integer", "element_type": "varchar",
            "tag_name": "varchar", "element_id": "varchar", "name": "varchar",
            "class_name": "varchar", "selector": "varchar", "text_content": "varchar",
            "placeholder": "varchar", "is_visible": "integer", "bounding_box": "text",
            "attributes": "text", "platform": "varchar", "selector_type": "varchar",
            "metadata": "text", "created_at": "datetime",
        },
        "fks": [("project_id", "projects", "cascade")],
        "uniques": [],
    },
    "test_cases": {
        "columns": {
            "id": "integer", "project_id": "integer", "case_name": "varchar",
            "case_no": "varchar", "priority": "varchar", "pre_condition": "text",
            "steps": "text", "expected_result": "text", "source_excel": "varchar",
            "excel_row": "integer", "status": "varchar", "created_at": "datetime",
            "updated_at": "datetime",
        },
        "fks": [("project_id", "projects", "cascade")],
        "uniques": [],
    },
    "generated_codes": {
        "columns": {
            "id": "integer", "case_id": "integer", "code_content": "text",
            "code_language": "varchar", "generation_prompt": "text", "ai_model": "varchar",
            "is_valid": "integer", "syntax_error": "text", "is_healed": "integer",
            "created_at": "datetime",
        },
        "fks": [("case_id", "test_cases", "cascade")],
        "uniques": [],
    },
    "executions": {
        "columns": {
            "id": "integer", "project_id": "integer", "batch_name": "varchar",
            "total_cases": "integer", "passed_cases": "integer", "failed_cases": "integer",
            "status": "varchar", "start_time": "datetime", "end_time": "datetime",
            "execution_mode": "varchar", "progress": "integer", "worker_id": "varchar",
            "heartbeat_at": "datetime", "created_at": "datetime",
        },
        "fks": [("project_id", "projects", "cascade")],
        "uniques": [],
    },
    "execution_steps": {
        "columns": {
            "id": "integer", "execution_id": "integer", "case_id": "integer",
            "step_index": "integer", "action": "varchar", "target_selector": "varchar",
            "input_value": "text", "status": "varchar", "screenshot_before": "varchar",
            "screenshot_after": "varchar", "log_output": "text", "error_message": "text",
            "exception_type": "varchar", "duration_ms": "integer", "created_at": "datetime",
        },
        "fks": [
            ("execution_id", "executions", "cascade"),
            ("case_id", "test_cases", "cascade"),
        ],
        "uniques": [],
    },
    "execution_reports": {
        "columns": {
            "id": "integer", "execution_id": "integer", "report_html": "text",
            "report_summary": "text", "download_url": "varchar", "created_at": "datetime",
        },
        "fks": [("execution_id", "executions", "cascade")],
        "uniques": [["execution_id"]],
    },
    "heal_records": {
        "columns": {
            "id": "integer", "execution_step_id": "integer", "original_code": "text",
            "error_context": "text", "healed_code": "text", "heal_prompt": "text",
            "retry_status": "varchar", "retry_count": "integer", "attempts": "text",
            "created_at": "datetime",
        },
        "fks": [("execution_step_id", "execution_steps", "cascade")],
        "uniques": [],
    },
}


# ── 字符串族（方言同义类型）──
# 同一「字符串大类型」的方言写法必须视为等价：旧库的 MySQL 写法
# （LONGTEXT / MEDIUMTEXT / JSON / TEXT）与 Baseline 规格（varchar / text）若按字面
# 比对会产生伪差异，从而误拦合法的 Bridge 升级。这些列在 ORM 中统一以
# Text / String 声明，方言差异不影响应用读写（0002 delta 也不改这些列的类型）。
_STRING_FAMILY = frozenset({
    "varchar", "string", "char", "nvarchar", "nchar", "clob",
    "text", "tinytext", "mediumtext", "longtext",
    "json", "jsonb",
})


def _norm_type(typ) -> str:
    """把反射出的类型归一化：类名小写、去长度/精度，字符串族统一归为 string。"""
    name = type(typ).__name__.lower()
    if name in _STRING_FAMILY:
        return "string"
    if name in ("integer", "bigint", "smallint", "tinyint"):
        return "integer"
    if name in ("datetime", "timestamp", "date"):
        return "datetime"
    if name in ("float", "numeric", "decimal", "double"):
        return "numeric"
    if name in ("boolean", "bool"):
        return "integer"
    return name


def _canon_baseline_type(name: str) -> str:
    """把 Baseline 规格里的字面类型名归一到与 _norm_type 相同的桶（字符串族 → string）。"""
    return "string" if (name or "").lower() in _STRING_FAMILY else (name or "").lower()


def _build_spec(conn) -> dict:
    """反射 live 库 → (表, 列, FK, 唯一约束) 规格。"""
    insp = sa.inspect(conn)
    spec: dict[str, dict] = {}
    for table in insp.get_table_names():
        cols = {c["name"]: _norm_type(c["type"]) for c in insp.get_columns(table)}
        fks = sorted(
            (fk["constrained_columns"][0], fk["referred_table"],
             (fk["options"].get("ondelete") or "restrict").lower())
            for fk in insp.get_foreign_keys(table)
        )
        uniques = sorted(sorted(u["column_names"]) for u in insp.get_unique_constraints(table))
        spec[table] = {"columns": cols, "fks": fks, "uniques": uniques}
    return spec


def compatibility_check(conn) -> tuple[bool, list[str]]:
    """逐表逐列逐约束比对 live 库与 Legacy Baseline。返回 (是否兼容, 差异清单)。"""
    diffs: list[str] = []
    live = _build_spec(conn)
    baseline_tables = set(_LEGACY_SCHEMA)

    for table in sorted(baseline_tables - set(live)):
        diffs.append("缺少表: %s（Legacy Baseline 应有）" % table)
    for table in sorted(set(live) - baseline_tables):
        diffs.append("多余表: %s（Legacy Baseline 未定义）" % table)

    for table in sorted(baseline_tables & set(live)):
        bl = _LEGACY_SCHEMA[table]
        lv = live[table]
        # 列
        missing_cols = sorted(set(bl["columns"]) - set(lv["columns"]))
        extra_cols = sorted(set(lv["columns"]) - set(bl["columns"]))
        for c in missing_cols:
            diffs.append("%s: 缺少列 %s" % (table, c))
        for c in extra_cols:
            diffs.append("%s: 多余列 %s（属 P0-11 Delta，旧库不应存在）" % (table, c))
        for c in sorted(set(bl["columns"]) & set(lv["columns"])):
            # 两侧都走同一字符串族归一（Baseline 字面量 vs live 反射类型）
            if _canon_baseline_type(bl["columns"][c]) != lv["columns"][c]:
                diffs.append(
                    "%s: 列 %s 类型不匹配（期望 %s，实际 %s）"
                    % (table, c, bl["columns"][c], lv["columns"][c])
                )
        # FK（比较列 + 父表 + ondelete）
        bl_fks = sorted(bl["fks"])
        if bl_fks != lv["fks"]:
            diffs.append(
                "%s: 外键不匹配（期望 %s，实际 %s）"
                % (table, bl_fks, lv["fks"])
            )
        # 唯一约束
        bl_uq = sorted(sorted(u) for u in bl["uniques"])
        if bl_uq != lv["uniques"]:
            diffs.append(
                "%s: 唯一约束不匹配（期望 %s，实际 %s）"
                % (table, bl_uq, lv["uniques"])
            )

    return (not diffs, diffs)


def has_alembic_version(conn) -> bool:
    """已由 Alembic 管理 = alembic_version 表存在且含版本行。

    旧库定义为『无 alembic_version』：表缺失或表存在但无版本行均视为旧库。
    """
    insp = sa.inspect(conn)
    if "alembic_version" not in insp.get_table_names():
        return False
    row = conn.execute(sa.text("SELECT version_num FROM alembic_version LIMIT 1")).first()
    return row is not None


def has_any_business_table(conn) -> bool:
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    return bool(tables & set(_LEGACY_SCHEMA))


def stamp_baseline(conn) -> None:
    """把 alembic_version 表写为 LEGACY_BASELINE_REVISION（只写版本表，不执行 migration）。"""
    if conn.dialect.name == "sqlite":
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        ))
    else:
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        ))
    conn.execute(sa.text("DELETE FROM alembic_version"))
    conn.execute(
        sa.text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
        {"v": LEGACY_BASELINE_REVISION},
    )
    conn.commit()
