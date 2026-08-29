"""Alembic 迁移环境 — 统一 MySQL / SQLite 双方言

数据库 URL 解析优先级：
  1. 环境变量 AUTOPILOT_ALEMBIC_URL（smoke test / 人工指定用）
  2. alembic.ini 中 sqlalchemy.url（默认不配置）
  3. app.config.settings.DATABASE_URL（backend/.env 或环境变量 DATABASE_URL）

SQLite 方言自动启用 render_as_batch=True：SQLite 不支持 DROP COLUMN / 修改列，
Alembic 通过"重建表"方式模拟这些操作。
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 将 backend 根目录加入 sys.path，保证 app 包可导入
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# 注册全部 ORM 模型到 metadata（必须在 target_metadata 前导入）
from app.db.database import Base  # noqa: E402
import app.models  # noqa: E402,F401
from app.config import settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False：迁移运行时不得禁用应用自身（autopilot.*）logger。
    # 否则在 pytest 进程内执行 alembic 命令时，fileConfig 会把未配置的 logger
    # 全部标记为 disabled，导致后续测试的日志捕获全部失效。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# ── 解析数据库 URL ──
_db_url = (
    os.environ.get("AUTOPILOT_ALEMBIC_URL")
    or config.get_main_option("sqlalchemy.url")
    or settings.DATABASE_URL
)

target_metadata = Base.metadata


def _is_sqlite(url: str) -> bool:
    """按 URL 判断 SQLite 方言（offline 模式无连接可查）"""
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Offline 模式：生成 SQL 脚本（不连接数据库）"""
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(_db_url),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online 模式：连接数据库执行迁移"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=_db_url,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
