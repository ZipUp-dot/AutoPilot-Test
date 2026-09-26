"""数据库连接池封装 — SQLAlchemy 同步引擎，支持 SQLite / MySQL / PostgreSQL

P0-11（Alembic 单一 Schema 权威）：
  - 新结构只由 Alembic 定义（alembic/versions/0001 + 0002_formal_delta）。
  - init() 启动时序：
      空库        → alembic upgrade head（0001 baseline 建完整 Legacy Schema →
                    0002 formal delta → 完整最终 Schema）
      旧库（无 alembic_version 但有业务表）→ Legacy Bridge（Baseline Compatibility
                    Check，不兼容 → 显式抛错并打印差异清单）→ stamp
                    LEGACY_BASELINE_REVISION → alembic upgrade head（只应用 0002，
                    结构正确且数据保留）
      已 Alembic 管理 → alembic upgrade head（幂等 no-op）
  - _run_migrations() 降级为 Legacy Bridge，只做旧库 baseline/stamp 判断，
    不再承担任何平行演进（禁止双轨演进）。
  - SQLite 引擎统一启用 PRAGMA foreign_keys=ON：删除父对象必须由 DB 层 FK
    RESTRICT 阻断，禁止 ORM 绕过数据库约束（passive_deletes 只删 cascade 不够）。
"""

import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from app.config import settings

logger = logging.getLogger("autopilot.db")

# ── SQLAlchemy 同步引擎 ──
# MySQL 需要额外参数；SQLite 需要 check_same_thread=False
_extra_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _extra_args["connect_args"] = {"check_same_thread": False}
    engine = create_engine(
        settings.DATABASE_URL,
        echo=False,
        **_extra_args,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - 事件监听
        """每个 SQLite 连接启用外键约束：RESTRICT 由数据库层强制。"""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    engine = create_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        **_extra_args,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Alembic 唯一权威 ──

def _backend_root() -> str:
    """backend/ 根目录（app/db/database.py → 上溯三级）。"""
    import os
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _alembic_config():
    """构造 Alembic Config：script_location 用绝对路径，URL 由 env.py 解析
    （AUTOPILOT_ALEMBIC_URL > alembic.ini > settings.DATABASE_URL）。"""
    import os
    from alembic.config import Config

    root = _backend_root()
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    return cfg


def _run_alembic_upgrade_head() -> None:
    """以 settings.DATABASE_URL 为目标执行 alembic upgrade head（幂等）。"""
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def _run_migrations() -> None:
    """Legacy Bridge：只做旧库 baseline/stamp 判断，不再承担任何平行演进。

    返回：
      - 已 Alembic 管理（有 alembic_version）→ 无操作
      - 空库（无任何业务表）→ 无操作（由 init() 的 alembic upgrade head 建库）
      - 旧库（无 alembic_version 但有业务表）→ Baseline Compatibility Check：
          · 兼容 → stamp LEGACY_BASELINE_REVISION（只写版本表，不执行 migration；
            禁止 stamp head 后再 upgrade head —— 正式 revision 永不执行）
          · 不兼容 → 显式抛错并打印差异清单，拒绝 stamp / 后续 upgrade
    """
    from app.db.legacy_baseline import (
        LEGACY_BASELINE_REVISION,
        compatibility_check,
        has_alembic_version,
        has_any_business_table,
        stamp_baseline,
    )

    with engine.connect() as conn:
        if has_alembic_version(conn):
            logger.info("数据库已由 Alembic 管理，跳过 Legacy Bridge")
            return
        if not has_any_business_table(conn):
            logger.info("空库，由 alembic upgrade head 建库")
            return
        ok, diffs = compatibility_check(conn)
        if not ok:
            detail = "\n".join(diffs)
            raise RuntimeError(
                "旧库与 Legacy Baseline（%s）不兼容，拒绝升级。差异清单：\n%s"
                % (LEGACY_BASELINE_REVISION, detail)
            )
        stamp_baseline(conn)
        logger.info(
            "旧库通过 Baseline Compatibility Check，已 stamp %s",
            LEGACY_BASELINE_REVISION,
        )


def init() -> None:
    """启动时初始化数据库 — Alembic 是唯一 Schema 权威（见模块 docstring）。"""
    _run_migrations()      # Legacy Bridge：旧库 baseline/stamp；空库/已管理库 no-op
    _run_alembic_upgrade_head()


# ── FastAPI 依赖注入 ──

def get_db() -> Session:
    """FastAPI 依赖：获取 SQLAlchemy 会话（同步）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
