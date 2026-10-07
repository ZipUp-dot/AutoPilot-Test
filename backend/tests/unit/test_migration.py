"""测试数据库迁移逻辑 — P0-11：Alembic 单一权威 + Legacy Bridge

P0-11 后 _run_migrations() 降级为 Legacy Bridge（只做旧库 baseline/stamp 判断），
不再承担任何平行演进 / ALTER 结构变更；新结构只由 Alembic 定义。

测试目标：
1. _run_migrations() 不再新增任何结构变更（代码审查证明：文件内无 ALTER / create_all）。
2. Legacy Bridge 四分支：已管理 / 空库 / 兼容旧库 / 不兼容旧库。
3. Alembic 语义：stamp 只写版本表，不执行 migration。
"""

import pytest
from pathlib import Path
from sqlalchemy import create_engine, inspect, text

_BACKEND_ROOT = Path(__file__).parents[2]  # tests/unit → backend


class TestLegacyBridgeNoStructuralChange:
    """_run_migrations() 不新增结构变更（双轨演进禁止证明）"""

    def test_source_contains_no_structural_mutation(self):
        """database.py 源码不含 ALTER TABLE / create_all / schema.sql 执行"""
        import inspect as pyinspect
        from app.db import database

        src = pyinspect.getsource(database)
        for banned in ("ALTER TABLE", "create_all", "schema.sql", "add_column",
                       "CREATE TABLE", "DROP TABLE"):
            assert banned not in src, f"_run_migrations 仍含结构变更关键字: {banned}"

    def test_init_only_bridge_and_alembic(self):
        """init() 仅由 _run_migrations + _run_alembic_upgrade_head 组成"""
        import inspect as pyinspect
        from app.db import database

        src = pyinspect.getsource(database.init)
        assert "_run_migrations()" in src
        assert "_run_alembic_upgrade_head()" in src


class TestLegacyBridgeBehavior:
    """Legacy Bridge 行为：兼容→stamp；不兼容→拒绝；已管理/空库→no-op"""

    def test_legacy_compatible_db_stamped_and_upgraded(self, tmp_path, monkeypatch):
        """完整 legacy 库（0001 结构）→ bridge stamp 0001 → upgrade head → 结构完整数据保留"""
        from alembic import command
        from alembic.config import Config
        from app.db.legacy_baseline import LEGACY_BASELINE_REVISION
        from app.db import database as dbmod

        root = Path(tmp_path)
        db = root / "legacy.db"
        url = f"sqlite:///{db.as_posix()}"

        cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
        monkeypatch.setenv("AUTOPILOT_ALEMBIC_URL", url)
        command.upgrade(cfg, "0001_initial_schema")

        # 模拟旧库：删除 alembic_version 表
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("INSERT INTO projects (id, name, target_url) VALUES (1, 'p1', 'http://x')"))
            conn.execute(text("DROP TABLE alembic_version"))
            conn.commit()
        eng.dispose()

        # Legacy Bridge：兼容 → stamp
        original_engine = dbmod.engine
        try:
            dbmod.engine = create_engine(url)
            dbmod._run_migrations()
            eng = dbmod.engine
            with eng.connect() as conn:
                v = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                assert v == LEGACY_BASELINE_REVISION, f"stamp 应为 {LEGACY_BASELINE_REVISION}"
                assert conn.execute(text("SELECT name FROM projects WHERE id=1")).scalar() == "p1"
            eng.dispose()

            # upgrade head：只应用 0002/0003/0004/0005 delta，结构正确且数据保留
            monkeypatch.setenv("AUTOPILOT_ALEMBIC_URL", url)
            command.upgrade(cfg, "head")
            eng = create_engine(url)
            try:
                insp = inspect(eng)
                tables = set(insp.get_table_names())
                assert {"batch_cases", "batch_records", "batch_jobs"} <= tables, tables
                with eng.connect() as conn:
                    assert conn.execute(text("SELECT name FROM projects WHERE id=1")).scalar() == "p1"
                    v = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    assert v == "0005_schedules", v
            finally:
                eng.dispose()
        finally:
            dbmod.engine = original_engine

    def test_legacy_incompatible_db_rejected(self, tmp_path, monkeypatch):
        """不完整旧库 → Compatibility Check 拒绝，并打印差异清单"""
        from app.db import database as dbmod

        db = Path(tmp_path) / "incomplete.db"
        url = f"sqlite:///{db.as_posix()}"
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL)"))
            conn.commit()
        eng.dispose()

        original_engine = dbmod.engine
        try:
            dbmod.engine = create_engine(url)
            with pytest.raises(RuntimeError) as exc_info:
                dbmod._run_migrations()
            msg = str(exc_info.value)
            assert "不兼容" in msg
            assert "缺少表: page_elements" in msg or "缺少列 platform" in msg
        finally:
            dbmod.engine = original_engine

    def test_stamp_does_not_execute_migration(self, tmp_path):
        """stamp 只写版本表：stamp 0001 后库中不应存在 batch 表"""
        from app.db.legacy_baseline import stamp_baseline, LEGACY_BASELINE_REVISION

        db = Path(tmp_path) / "stamp_only.db"
        url = f"sqlite:///{db.as_posix()}"
        eng = create_engine(url)
        with eng.connect() as conn:
            stamp_baseline(conn)
        with eng.connect() as conn:
            v = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert v == LEGACY_BASELINE_REVISION
            tables = {r[0] for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'"))}
            assert "projects" not in tables, "stamp 不应创建任何业务表"
        eng.dispose()


class _FakeInspector:
    """以指定类型反射 Baseline 规格结构（用于模拟 MySQL 旧库的方言类型写法）"""

    def __init__(self, types: dict[str, dict]) -> None:
        self._types = types

    def get_table_names(self):
        return list(self._types)

    def get_columns(self, table):
        return [{"name": c, "type": t} for c, t in self._types[table].items()]

    def get_foreign_keys(self, table):
        from app.db.legacy_baseline import _LEGACY_SCHEMA
        return [
            {"constrained_columns": [col], "referred_table": parent,
             "options": {"ondelete": "CASCADE" if od == "cascade" else "RESTRICT"}}
            for (col, parent, od) in _LEGACY_SCHEMA[table]["fks"]
        ]

    def get_unique_constraints(self, table):
        from app.db.legacy_baseline import _LEGACY_SCHEMA
        return [{"column_names": list(u)} for u in _LEGACY_SCHEMA[table]["uniques"]]


class TestLegacyTypeDialectNormalization:
    """字符串族方言写法（LONGTEXT / JSON / TEXT）不得与 Baseline（varchar / text）产生伪差异"""

    def test_mysql_string_family_normalized_to_one_bucket(self):
        from sqlalchemy.dialects import mysql
        from app.db.legacy_baseline import _norm_type, _canon_baseline_type

        for mysql_type in (mysql.VARCHAR(500), mysql.TEXT(), mysql.LONGTEXT(),
                           mysql.MEDIUMTEXT(), mysql.JSON()):
            assert _norm_type(mysql_type) == "string", mysql_type
        assert _canon_baseline_type("varchar") == "string"
        assert _canon_baseline_type("text") == "string"
        # 非字符串族仍保持各自大类型（不得被一并放宽）
        assert _norm_type(mysql.INTEGER()) == "integer"
        assert _norm_type(mysql.DATETIME()) == "datetime"

    def test_mysql_dialect_legacy_db_passes_compatibility_check(self, mocker):
        """复现真实报错场景：MySQL 旧库字符串族写法差异 → 不再判为不兼容"""
        from sqlalchemy.dialects import mysql
        import app.db.legacy_baseline as lb

        reported = {
            "execution_reports": {"report_html": mysql.LONGTEXT(),
                                  "report_summary": mysql.JSON()},
            "execution_steps": {"target_selector": mysql.TEXT()},
            "generated_codes": {"code_content": mysql.LONGTEXT()},
            "heal_records": {"error_context": mysql.JSON()},
            "page_elements": {"attributes": mysql.JSON(), "bounding_box": mysql.JSON(),
                              "selector": mysql.TEXT(), "text_content": mysql.TEXT()},
            "test_cases": {"steps": mysql.JSON()},
        }
        by_baseline_type = {"varchar": mysql.VARCHAR(500), "text": mysql.TEXT(),
                            "integer": mysql.INTEGER(), "datetime": mysql.DATETIME()}
        types = {
            table: {
                col: reported.get(table, {}).get(col, by_baseline_type[t])
                for col, t in bl["columns"].items()
            }
            for table, bl in lb._LEGACY_SCHEMA.items()
        }

        mocker.patch("app.db.legacy_baseline.sa.inspect",
                     return_value=_FakeInspector(types))
        ok, diffs = lb.compatibility_check(object())
        assert ok is True, f"方言字符串族差异不应被判为不兼容: {diffs}"
        assert diffs == []

    def test_non_string_type_mismatch_still_rejected(self, mocker):
        """反向保护：真正的类型族不匹配（integer vs string）仍必须拒绝"""
        from sqlalchemy.dialects import mysql
        import app.db.legacy_baseline as lb

        by_baseline_type = {"varchar": mysql.VARCHAR(500), "text": mysql.TEXT(),
                            "integer": mysql.INTEGER(), "datetime": mysql.DATETIME()}
        types = {
            table: {col: by_baseline_type[t] for col, t in bl["columns"].items()}
            for table, bl in lb._LEGACY_SCHEMA.items()
        }
        types["projects"]["id"] = mysql.TEXT()  # 期望 integer，实际字符串族

        mocker.patch("app.db.legacy_baseline.sa.inspect",
                     return_value=_FakeInspector(types))
        ok, diffs = lb.compatibility_check(object())
        assert ok is False
        assert any("projects: 列 id 类型不匹配" in d for d in diffs), diffs
