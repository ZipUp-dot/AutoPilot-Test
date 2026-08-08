"""数据库连接池封装 — SQLAlchemy 同步引擎，支持 SQLite / MySQL / PostgreSQL"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from app.config import settings

# ── SQLAlchemy 同步引擎 ──
# MySQL 需要额外参数；SQLite 需要 check_same_thread=False
_extra_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _extra_args["connect_args"] = {"check_same_thread": False}

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


def init() -> None:
    """启动时初始化数据库：优先使用 schema.sql，否则 Base.metadata.create_all()

    对于 MySQL，建议先手动创建数据库：
        CREATE DATABASE autopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    """
    import os

    current_dir = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(current_dir, "schema.sql")

    if os.path.exists(schema_path):
        # 读取 schema.sql 并执行（跳过 SQLite 专用语法）
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()

        # 如果是 MySQL，跳过 SQLite PRAGMA 和触发器
        if not settings.DATABASE_URL.startswith("sqlite"):
            import re
            # 移除 SQLite 触发器（MySQL 语法不同）
            sql = re.sub(
                r'-- =+.*?=+\s*CREATE TRIGGER.*?END;\s*',
                '',
                sql,
                flags=re.DOTALL,
            )

        with engine.connect() as conn:
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(statement=stmt)
                    except Exception:
                        pass  # 表已存在则跳过
            conn.commit()
    else:
        Base.metadata.create_all(bind=engine)


# ── FastAPI 依赖注入 ──

def get_db() -> Session:
    """FastAPI 依赖：获取 SQLAlchemy 会话（同步）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()