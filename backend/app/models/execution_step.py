"""执行步骤 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class ExecutionStep(Base):
    __tablename__ = "execution_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("executions.id", ondelete="CASCADE"), nullable=False)
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    step_index = Column(Integer, nullable=False)
    action = Column(String)
    target_selector = Column(String)
    input_value = Column(String)
    status = Column(String, default="pending")
    screenshot_before = Column(String)
    screenshot_after = Column(String)
    log_output = Column(String)
    error_message = Column(String)
    exception_type = Column(String)
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
