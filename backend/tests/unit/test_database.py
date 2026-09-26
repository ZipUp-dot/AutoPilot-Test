"""数据库初始化测试 — 覆盖 app/db/database.py 的 init() 和 get_db()（P0-11）

P0-11 之后：
  - init() = _run_migrations()（Legacy Bridge）+ _run_alembic_upgrade_head()，
    不再执行 schema.sql / create_all / 平行 ALTER。
  - SQLite 引擎注册 PRAGMA foreign_keys=ON 事件监听。
"""

import pytest
from sqlalchemy.orm import Session


class TestDatabaseInit:
    """init() 生命周期测试（P0-11：Alembic 唯一权威）"""

    def test_init_runs_legacy_bridge_then_alembic_upgrade(self, mocker):
        """init() 依次调用 _run_migrations() 与 _run_alembic_upgrade_head()"""
        from app.db.database import init

        mock_bridge = mocker.patch("app.db.database._run_migrations")
        mock_upgrade = mocker.patch("app.db.database._run_alembic_upgrade_head")

        init()

        mock_bridge.assert_called_once_with()
        mock_upgrade.assert_called_once_with()

    def test_init_propagates_bridge_failure(self, mocker):
        """旧库 Compatibility Check 不兼容 → init() 显式抛错（不静默吞错）"""
        from app.db.database import init

        mocker.patch("app.db.database._run_migrations",
                     side_effect=RuntimeError("旧库与 Legacy Baseline 不兼容"))
        mock_upgrade = mocker.patch("app.db.database._run_alembic_upgrade_head")

        with pytest.raises(RuntimeError, match="不兼容"):
            init()
        mock_upgrade.assert_not_called()

    def test_init_propagates_alembic_failure(self, mocker):
        """alembic upgrade head 失败 → init() 显式抛错（不吞错）"""
        from app.db.database import init

        mocker.patch("app.db.database._run_migrations")
        mocker.patch("app.db.database._run_alembic_upgrade_head",
                     side_effect=RuntimeError("migration failed"))

        with pytest.raises(RuntimeError, match="migration failed"):
            init()


class TestLegacyBridge:
    """_run_migrations() 降级为 Legacy Bridge：不再新增任何结构变更"""

    def test_alembic_managed_db_returns_without_structural_change(self, mocker):
        """已 Alembic 管理（有 alembic_version）→ 直接返回，不 stamp 不升级"""
        from app.db.database import _run_migrations

        mock_conn = mocker.MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mock_has_ver = mocker.patch("app.db.legacy_baseline.has_alembic_version",
                                    return_value=True)
        mock_stamp = mocker.patch("app.db.legacy_baseline.stamp_baseline")
        mock_check = mocker.patch("app.db.legacy_baseline.compatibility_check")

        _run_migrations()

        mock_has_ver.assert_called_once_with(mock_conn)
        mock_stamp.assert_not_called()
        mock_check.assert_not_called()

    def test_empty_db_returns_without_structural_change(self, mocker):
        """空库（无业务表）→ 直接返回，交 alembic upgrade head 建库"""
        from app.db.database import _run_migrations

        mock_conn = mocker.MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mocker.patch("app.db.legacy_baseline.has_alembic_version", return_value=False)
        mocker.patch("app.db.legacy_baseline.has_any_business_table", return_value=False)
        mock_stamp = mocker.patch("app.db.legacy_baseline.stamp_baseline")
        mock_check = mocker.patch("app.db.legacy_baseline.compatibility_check")

        _run_migrations()

        mock_stamp.assert_not_called()
        mock_check.assert_not_called()

    def test_legacy_compatible_db_stamps_baseline(self, mocker):
        """旧库通过 Compatibility Check → stamp LEGACY_BASELINE_REVISION"""
        from app.db.database import _run_migrations

        mock_conn = mocker.MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mocker.patch("app.db.legacy_baseline.has_alembic_version", return_value=False)
        mocker.patch("app.db.legacy_baseline.has_any_business_table", return_value=True)
        mocker.patch("app.db.legacy_baseline.compatibility_check", return_value=(True, []))
        mock_stamp = mocker.patch("app.db.legacy_baseline.stamp_baseline")

        _run_migrations()

        mock_stamp.assert_called_once_with(mock_conn)

    def test_legacy_incompatible_db_raises_with_diff_list(self, mocker):
        """旧库不兼容 → 显式抛错并携带差异清单（禁止静默吞迁移错误）"""
        from app.db.database import _run_migrations

        mock_conn = mocker.MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        mocker.patch("app.db.legacy_baseline.has_alembic_version", return_value=False)
        mocker.patch("app.db.legacy_baseline.has_any_business_table", return_value=True)
        diffs = ["缺少表: page_elements（Legacy Baseline 应有）",
                 "projects: 缺少列 platform"]
        mocker.patch("app.db.legacy_baseline.compatibility_check", return_value=(False, diffs))
        mock_stamp = mocker.patch("app.db.legacy_baseline.stamp_baseline")

        with pytest.raises(RuntimeError, match="缺少表: page_elements"):
            _run_migrations()
        mock_stamp.assert_not_called()


class TestSqliteForeignKeyPragma:
    """SQLite 引擎 PRAGMA foreign_keys=ON（engine event listener）"""

    def test_connect_listener_enables_foreign_keys(self):
        """每次连接建立时执行 PRAGMA foreign_keys=ON"""
        import sqlalchemy as sa
        from sqlalchemy import event, create_engine

        calls = []
        eng = create_engine("sqlite:///:memory:")

        @event.listens_for(eng, "connect")
        def _hook(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
            calls.append(True)

        conn = eng.connect()
        cur = conn.connection.cursor()
        value = cur.execute("PRAGMA foreign_keys").fetchone()[0]
        cur.close()
        conn.close()
        eng.dispose()

        assert calls, "事件监听未触发"
        assert value == 1, f"PRAGMA foreign_keys 应为 ON，实际 {value}"


class TestGetDb:
    """get_db() 依赖注入测试"""

    def test_get_db_yields_session_and_closes(self, mocker):
        """get_db() 返回 Session 并在退出时关闭"""
        from app.db.database import get_db

        mock_session = MagicMock(spec=Session)
        # 使用模块级 patch 替换 SessionLocal，避免 sessionmaker 的 __call__ 问题
        mocker.patch("app.db.database.SessionLocal", return_value=mock_session)

        gen = get_db()
        session = next(gen)

        assert session is mock_session

        try:
            next(gen)
        except StopIteration:
            pass

        mock_session.close.assert_called_once()

    def test_get_db_closes_on_exception(self, mocker):
        """get_db() 在异常时也关闭 Session"""
        from app.db.database import get_db

        mock_session = MagicMock(spec=Session)
        mocker.patch("app.db.database.SessionLocal", return_value=mock_session)

        gen = get_db()
        session = next(gen)
        assert session is mock_session

        # 模拟异常后 generator 退出
        with pytest.raises(RuntimeError):
            gen.throw(RuntimeError, "test error")

        mock_session.close.assert_called_once()


class TestEngineCreation:
    """SQLAlchemy 引擎创建测试"""

    def test_base_has_metadata(self):
        """Base 有 metadata 属性"""
        from app.db.database import Base
        assert hasattr(Base, "metadata")
        assert len(Base.metadata.tables) > 0

    def test_session_local_is_callable(self):
        """SessionLocal 是可调用的"""
        from app.db.database import SessionLocal
        assert callable(SessionLocal)


# 兼容旧导入：MagicMock 由 unittest.mock 提供
from unittest.mock import MagicMock
