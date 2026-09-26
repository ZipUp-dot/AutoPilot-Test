"""Batch 历史 KPI 记录 — ORM 模型 + Pydantic V2 Schema

Batch 级终态事实：一个 Batch 收口时，将 summary 以 batch_records 落库，使其能回答
「这个 Batch 当时最终是 completed 还是 failed」。写入发生在全部 BatchCase terminal
之后，DB 事务 COMMIT 成功前，不得先标 completed 再写 DB。

Schema Delta（待任务 11 Alembic 收口，禁止动 schema.sql）：
  - 新建表 batch_records：
      id          INTEGER PK AUTO_INCREMENT
      project_id  INT NOT NULL, FK projects.id ON DELETE CASCADE
      batch_id    VARCHAR(64) NOT NULL
      batch_status VARCHAR(20) NOT NULL   -- completed / failed（与 BatchJob 终态同值写入）
      summary_json TEXT NOT NULL          -- 见 SummarySchema，10 项 KPI + 逐 Case 明细
      created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      UNIQUE KEY uq_batch_records_batch_id (batch_id)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class BatchRecord(Base):
    __tablename__ = "batch_records"
    __table_args__ = (
        UniqueConstraint("batch_id", name="uq_batch_records_batch_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：BatchRecord→Project RESTRICT
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    batch_id = Column(String(64), nullable=False)
    batch_status = Column(String(20), nullable=False)
    summary_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class BatchRecordResponse(BaseModel):
    id: int
    project_id: int
    batch_id: str
    batch_status: str
    summary_json: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}