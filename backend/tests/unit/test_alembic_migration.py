"""Alembic 迁移 smoke test — 阶段 9 统一数据库迁移机制

覆盖主链路（MySQL / SQLite 双方言）：
  1. 空 SQLite → alembic upgrade head → 全部表 + alembic_version 创建成功
  2. alembic downgrade base → 表删除（可回滚）
  3. 已有库（create_all / schema.sql 已建表）→ upgrade head 平滑跳过，不报错
  4. alembic revision --autogenerate 命令可用（生成新 revision）
  5. 空 MySQL → alembic upgrade head → 成功（MySQL 可用时；不可用则 skip）
  6. MySQL offline 模式生成 SQL（无需真实连接，始终可验证 MySQL 方言）

说明：
  - 测试库仍使用 Base.metadata.create_all()（见 conftest），本文件只验证迁移主链路。
  - 数据库 URL 通过环境变量 AUTOPILOT_ALEMBIC_URL 注入（env.py 优先级最高）。
  - MySQL 测试使用独立临时库，结束后删除，不影响真实业务库。
"""

import os
import random
import string
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

_BACKEND_ROOT = Path(__file__).parents[2]  # tests/unit → backend


def _alembic_config() -> Config:
    """构造 Alembic Config（script_location 用绝对路径，避免 cwd 依赖）"""
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def _run_upgrade(url: str, monkeypatch) -> None:
    """对指定 URL 执行 alembic upgrade head（URL 经 env var 注入，env.py 优先级最高）"""
    cfg = _alembic_config()
    monkeypatch.setenv("AUTOPILOT_ALEMBIC_URL", url)
    command.upgrade(cfg, "head")


def _run_downgrade(url: str, monkeypatch) -> None:
    """对指定 URL 执行 alembic downgrade base"""
    cfg = _alembic_config()
    monkeypatch.setenv("AUTOPILOT_ALEMBIC_URL", url)
    command.downgrade(cfg, "base")


_ALL_TABLES = {
    "projects",
    "page_elements",
    "test_cases",
    "generated_codes",
    "executions",
    "execution_steps",
    "execution_reports",
    "heal_records",
}


def _assert_full_schema(url: str) -> None:
    """断言 8 张业务表 + alembic_version 全部存在，且含关键列"""
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        missing = _ALL_TABLES - tables
        assert not missing, f"缺少表: {missing}"
        assert "alembic_version" in tables, "缺少 alembic_version 表"

        proj_cols = {c["name"] for c in inspector.get_columns("projects")}
        assert {"platform", "config_json", "created_at"}.issubset(proj_cols), proj_cols

        exe_cols = {c["name"] for c in inspector.get_columns("executions")}
        assert {"progress", "worker_id", "heartbeat_at"}.issubset(exe_cols), exe_cols

        heal_cols = {c["name"] for c in inspector.get_columns("heal_records")}
        assert "attempts" in heal_cols, "heal_records 缺少 attempts 列"
    finally:
        engine.dispose()


class TestSQLiteMigration:
    """SQLite 迁移主链路（render_as_batch=True 已在 env.py 对 sqlite 启用）"""

    def _sqlite_url(self, tmp_path) -> str:
        db = tmp_path / "test.db"
        return f"sqlite:///{db.as_posix()}"

    def test_empty_sqlite_upgrade_head(self, tmp_path, monkeypatch):
        """空 SQLite → alembic upgrade head → 全部表创建成功"""
        url = self._sqlite_url(tmp_path)
        _run_upgrade(url, monkeypatch)
        _assert_full_schema(url)

    def test_sqlite_downgrade_base_drops_tables(self, tmp_path, monkeypatch):
        """upgrade head → downgrade base → 业务表全部删除（可回滚）"""
        url = self._sqlite_url(tmp_path)
        _run_upgrade(url, monkeypatch)
        _run_downgrade(url, monkeypatch)

        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
            assert _ALL_TABLES.isdisjoint(tables), f"downgrade 后仍存在表: {_ALL_TABLES & tables}"
        finally:
            engine.dispose()

    def test_sqlite_upgrade_smooth_on_existing_schema(self, tmp_path, monkeypatch):
        """已有库（create_all 建表）→ upgrade head 平滑跳过，不报错"""
        url = self._sqlite_url(tmp_path)

        # 模拟已有 schema.sql / create_all 初始化过的库
        from app.db.database import Base
        import app.models  # noqa: F401 — 注册全部模型

        engine = create_engine(url)
        Base.metadata.create_all(bind=engine)
        engine.dispose()

        # 对已有表执行 upgrade head：表已存在应跳过，不抛错
        _run_upgrade(url, monkeypatch)
        _assert_full_schema(url)

    def test_alembic_revision_autogenerate(self, tmp_path, monkeypatch):
        """alembic revision --autogenerate 命令可用（生成新 revision 文件）

        使用复制到临时目录的脚本，autogenerate 产物写入临时 versions/，
        不污染仓库真实版本目录。
        """
        import shutil

        db = tmp_path / "rev.db"
        url = f"sqlite:///{db.as_posix()}"

        # 复制脚本目录到临时位置（env.py 中 backend root 推导对测试无影响：
        # app 包已由 pytest 的真实 sys.path 提供）
        tmp_alembic = tmp_path / "alembic"
        shutil.copytree(_BACKEND_ROOT / "alembic", tmp_alembic)

        cfg = _alembic_config()
        cfg.set_main_option("script_location", str(tmp_alembic))
        monkeypatch.setenv("AUTOPILOT_ALEMBIC_URL", url)
        command.upgrade(cfg, "head")

        command.revision(
            cfg,
            autogenerate=True,
            message="autogen check",
            rev_id="9999_autogen_check",
        )
        files = list((tmp_alembic / "versions").glob("*.py"))
        assert any("9999_autogen_check" in f.name for f in files), \
            f"应生成新 revision 文件: {[f.name for f in files]}"


def _resolve_mysql_server_url() -> str | None:
    """解析 MySQL server URL（不含库名）。

    优先级：
      1. 环境变量 AUTOPILOT_MYSQL_TEST_URL（CI / 手动指定）
      2. backend/.env 或当前 settings 中的 DATABASE_URL（本地开发环境）
    非 MySQL 配置返回 None。
    """
    if os.environ.get("AUTOPILOT_MYSQL_TEST_URL"):
        return os.environ["AUTOPILOT_MYSQL_TEST_URL"]

    candidates: list[str] = []
    from app.config import settings

    if settings.DATABASE_URL:
        candidates.append(settings.DATABASE_URL)

    env_file = _BACKEND_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("DATABASE_URL=") and not stripped.startswith("#"):
                candidates.append(stripped.split("=", 1)[1].strip())

    for raw in candidates:
        url = make_url(raw)
        if url.drivername.startswith("mysql") and url.host:
            return (
                f"mysql+pymysql://{url.username}:{url.password}"
                f"@{url.host}:{url.port or 3306}/"
            )
    return None


class TestMySQLMigration:
    """MySQL 迁移主链路（本地/CI 有 MySQL 时真连验证，否则 skip）"""

    @pytest.fixture
    def mysql_env(self):
        """连接本地 MySQL，创建临时测试库，返回完整 URL"""
        server_url = _resolve_mysql_server_url()
        if not server_url:
            pytest.skip("未配置 MySQL（AUTOPILOT_MYSQL_TEST_URL 或 .env 的 DATABASE_URL）")

        import pymysql

        url = make_url(server_url)
        db_name = "autopilot_alembic_test_" + "".join(
            random.choices(string.digits, k=4)
        )
        try:
            conn = pymysql.connect(
                host=url.host,
                port=url.port or 3306,
                user=url.username,
                password=url.password or "",
                connect_timeout=5,
            )
        except Exception as exc:  # noqa: BLE001 — 无 MySQL 则跳过
            pytest.skip(f"无法连接 MySQL: {exc}")

        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{db_name}` "
                            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            conn.commit()
        finally:
            conn.close()

        full_url = f"{server_url.rstrip('/')}/{db_name}"
        yield full_url

        # 清理：删除临时测试库
        try:
            conn = pymysql.connect(
                host=url.host,
                port=url.port or 3306,
                user=url.username,
                password=url.password or "",
                connect_timeout=5,
            )
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001 — 清理失败不影响测试结果
            pass

    def test_mysql_upgrade_head_and_downgrade(self, mysql_env, monkeypatch):
        """空 MySQL → alembic upgrade head → 全部表创建 → downgrade base → 回滚"""
        url = mysql_env
        _run_upgrade(url, monkeypatch)
        _assert_full_schema(url)

        # 验证 MySQL 关键类型：TEXT 列无默认值（MySQL 8.0 限制）
        engine = create_engine(url)
        try:
            cols = {c["name"]: c for c in inspect(engine).get_columns("heal_records")}
            assert cols["attempts"]["type"].__class__.__name__ == "TEXT"
        finally:
            engine.dispose()

        _run_downgrade(url, monkeypatch)
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
            assert _ALL_TABLES.isdisjoint(tables), f"downgrade 后仍存在表: {_ALL_TABLES & tables}"
        finally:
            engine.dispose()


class TestMySQLOffline:
    """MySQL offline 模式（--sql）— 无需真实连接即可验证 MySQL 方言 DDL"""

    def test_mysql_offline_sql_generation(self, capsys, monkeypatch):
        """alembic upgrade head --sql 生成含 CREATE TABLE projects 的 MySQL SQL"""
        cfg = _alembic_config()
        monkeypatch.setenv(
            "AUTOPILOT_ALEMBIC_URL",
            "mysql+pymysql://root:pw@127.0.0.1:3306/autopilot_offline_test",
        )
        command.upgrade(cfg, "head", sql=True)

        sql = capsys.readouterr().out
        assert "CREATE TABLE projects" in sql, "生成的 SQL 应包含 projects 建表语句"
        for table in _ALL_TABLES:
            assert f"CREATE TABLE {table}" in sql, f"生成的 SQL 缺少 {table}"
