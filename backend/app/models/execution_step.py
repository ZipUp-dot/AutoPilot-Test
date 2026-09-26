"""执行步骤 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Index, UniqueConstraint
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class ExecutionStep(Base):
    __tablename__ = "execution_steps"
    __table_args__ = (
        # 与 schema.sql / alembic 0001 索引对齐（保证 autogenerate 零 diff）
        Index("idx_es_execution_id", "execution_id"),
        Index("idx_es_case_id", "case_id"),
        # P0-11：同执行内每个 case 的 step_index 唯一（DB 级约束，防重复步骤事实）
        UniqueConstraint("execution_id", "case_id", "step_index",
                         name="uq_execution_steps_execution_id_case_id_step_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：ExecutionStep→Execution/TestCase RESTRICT
    execution_id = Column(Integer, ForeignKey("executions.id", ondelete="RESTRICT"), nullable=False)
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False)
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
    # P0-6 物化契约：步骤断言快照 + 跳过/失败原因（Admission 时落定）
    assertion = Column(Text)
    skip_reason = Column(String(50))
    error_type = Column(String(50))
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
    assertion: Optional[str] = None
    skip_reason: Optional[str] = None
    error_type: Optional[str] = None
    duration_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}
