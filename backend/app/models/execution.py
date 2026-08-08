"""执行批次 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    batch_name = Column(String)
    total_cases = Column(Integer, default=0)
    passed_cases = Column(Integer, default=0)
    failed_cases = Column(Integer, default=0)
    status = Column(String, default="running")
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    execution_mode = Column(String, default="headless")
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class ExecutionCreate(BaseModel):
    case_ids: list[int] = Field(..., min_length=1)
    batch_name: Optional[str] = None
    execution_mode: str = Field(default="headless")


class ExecutionResponse(BaseModel):
    id: int
    project_id: int
    batch_name: Optional[str]
    total_cases: int
    passed_cases: int
    failed_cases: int
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    execution_mode: str
    created_at: datetime

    model_config = {"from_attributes": True}
