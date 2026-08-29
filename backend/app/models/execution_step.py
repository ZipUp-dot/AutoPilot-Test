"""执行步骤 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Index
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class ExecutionStep(Base):
    __tablename__ = "execution_steps"
    __table_args__ = (
        # 与 schema.sql / alembic 0001 索引对齐（保证 autogenerate 零 diff）
        Index("idx_es_execution_id", "execution_id"),
        Index("idx_es_case_id", "case_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("executions.id", ondelete="CASCADE"), nullable=False)
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    step_index = Column(Integer, nullable=False)
    action = Column(String(50))
    target_selector = Column(String(500))
    input_value = Column(Text)
    status = Column(String(20), default="pending")
    screenshot_before = Column(String(500))
    screenshot_after = Column(String(500))
    log_output = Column(Text)
    error_message = Column(Text)
    exception_type = Column(String(100))
    duration_ms = Column(Integer)
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class ExecutionStepCreate(BaseModel):
    case_id: int
    step_index: int
    action: Optional[str] = None
    target_selector: Optional[str] = None
    input_value: Optional[str] = None


class ExecutionStepResponse(BaseModel):
    id: int
    execution_id: int
    case_id: int
    step_index: int
    action: Optional[str]
    target_selector: Optional[str]
    input_value: Optional[str]
    status: str
    screenshot_before: Optional[str]
    screenshot_after: Optional[str]
    log_output: Optional[str]
    error_message: Optional[str]
    exception_type: Optional[str] = None
    duration_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}
