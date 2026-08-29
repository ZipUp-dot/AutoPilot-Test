"""自愈记录 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class HealRecord(Base):
    __tablename__ = "heal_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_step_id = Column(Integer, ForeignKey("execution_steps.id", ondelete="CASCADE"), nullable=False)
    original_code = Column(Text)
    error_context = Column(Text)
    healed_code = Column(Text)
    heal_prompt = Column(Text)
    retry_status = Column(String(20), default="pending")
    retry_count = Column(Integer, default=0)
    attempts = Column(Text, default="[]")
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class HealRecordCreate(BaseModel):
    execution_step_id: int
    original_code: Optional[str] = None
    error_context: Optional[dict] = None
    healed_code: Optional[str] = None
    heal_prompt: Optional[str] = None
    retry_count: int = 0


class HealRecordResponse(BaseModel):
    id: int
    execution_step_id: int
    original_code: Optional[str]
    error_context: Optional[dict]
    healed_code: Optional[str]
    heal_prompt: Optional[str]
    retry_status: str
    retry_count: int
    created_at: datetime

    model_config = {"from_attributes": True}
