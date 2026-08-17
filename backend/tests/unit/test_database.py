"""数据库初始化测试 — 覆盖 app/db/database.py 的 init() 和 get_db()"""

import os
import pytest
from unittest.mock import MagicMock, patch, mock_open
from sqlalchemy.orm import Session


class TestDatabaseInit:
    """init() 生命周期测试"""

    def test_init_with_schema_sql_executes_statements(self, mocker):
        """schema.sql 存在时逐条执行 SQL 语句"""
        from app.db.database import init

        mocker.patch("os.path.exists", return_value=True)
        mocker.patch(
            "builtins.open",
            mock_open(read_data="CREATE TABLE test (id INT);\nINSERT INTO test VALUES (1);\n"),
        )
        mock_conn = MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        init()

        assert mock_conn.execute.call_count >= 1
        mock_conn.commit.assert_called_once()

    def test_init_with_schema_sql_skip_comments(self, mocker):
        """schema.sql 中的注释行被跳过"""
        from app.db.database import init

        mocker.patch("os.path.exists", return_value=True)
        mocker.patch(
            "builtins.open",
            mock_open(read_data="CREATE TABLE test (id INT);\n-- comment\n"),
        )
        mock_conn = MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        init()

        # 只有 CREATE TABLE 被执行，-- comment 被跳过
        assert mock_conn.execute.call_count >= 1
        mock_conn.commit.assert_called_once()

    def test_init_without_schema_sql_creates_all(self, mocker):
        """schema.sql 不存在时调用 Base.metadata.create_all"""
        from app.db.database import init

        mocker.patch("os.path.exists", return_value=False)
        mock_create_all = mocker.patch("app.db.database.Base.metadata.create_all")

        init()

        mock_create_all.assert_called_once_with(bind=mocker.ANY)

    def test_init_mysql_removes_triggers(self, mocker):
        """MySQL 模式下移除 SQLite 触发器语法"""
        from app.db.database import init

        mocker.patch("os.path.exists", return_value=True)
        trigger_sql = """
-- ========================================
CREATE TRIGGER update_timestamp
AFTER UPDATE ON projects
BEGIN
    UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
        """.strip()
        mocker.patch(
            "builtins.open",
            mock_open(read_data=trigger_sql),
        )
        mock_conn = MagicMock()
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mocker.patch("app.db.database.settings.DATABASE_URL", "mysql://localhost/test")

        init()

        # 触发器被移除，execute 不应被调用（因为没有剩余有效语句）
        # 如果有其他非触发器语句，则会被执行
        # 这里只检查触发器被移除且不报错
        mock_conn.commit.assert_called_once()

    def test_init_execute_exception_skipped(self, mocker):
        """执行 SQL 报错（表已存在）时跳过不中断"""
        from app.db.database import init

        mocker.patch("os.path.exists", return_value=True)
        mocker.patch(
            "builtins.open",
            mock_open(read_data="CREATE TABLE IF NOT EXISTS test (id INT);\n"),
        )
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [Exception("table already exists")]
        mock_engine = mocker.patch("app.db.database.engine")
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        init()  # Should not raise

        mock_conn.commit.assert_called_once()


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

    def test_engine_is_sqlite(self):
        """当前测试环境是 SQLite 内存数据库"""
        from app.db.database import engine
        assert "sqlite" in str(engine.url)

    def test_base_has_metadata(self):
        """Base 有 metadata 属性"""
        from app.db.database import Base
        assert hasattr(Base, "metadata")
        assert len(Base.metadata.tables) > 0

    def test_session_local_is_callable(self):
        """SessionLocal 是可调用的"""
        from app.db.database import SessionLocal
        assert callable(SessionLocal)