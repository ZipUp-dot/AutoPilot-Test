"""Batch 运行态元信息 — ORM 模型（服务重启续跑的事实载体）

batch_cases / batch_records 记录逐 Case 与 Batch 的「终态历史」；batch_jobs 记录
Batch 的「运行态元信息」：原始请求的 case 集合 + 当前 status。创建即写 status=running，
finalize 时更新为 completed/failed。服务启动时扫描 status=running 的 open job，
据此重建内存 BatchJob 并「续跑」未终态的用例（解决内存线程任务随进程重启丢失的问题）。

Schema：Alembic 0003_batch_jobs（新表，统一走 Alembic，禁止动 schema.sql）。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class BatchJobModel(Base):
    __tablename__ = "batch_jobs"
    __table_args__ = (
        # 一个 batch 只维护一行运行态元信息
        UniqueConstraint("batch_id", name="uq_batch_jobs_batch_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：BatchJobModel→Project RESTRICT
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    batch_id = Column(String(64), nullable=False)
    # 原始请求的 case 集合（JSON list[int]），供重启后重建与 diff 未终态用例
    case_ids = Column(Text, nullable=False)
    # running / finalizing / completed / failed（与 BatchJob 运行态对齐；终态 st→ completed/failed）
    status = Column(String(20), nullable=False, default="running")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now())
    terminal_at = Column(DateTime, nullable=True)


# ── Pydantic V2 Schema ──

class BatchJobModelResponse(BaseModel):
    id: int
    project_id: int
    batch_id: str
    case_ids: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    terminal_at: Optional[datetime] = None

    model_config = {"from_attributes": True}