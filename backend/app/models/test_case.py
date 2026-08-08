"""测试用例 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from pydantic import BaseModel, Field

from app.db.database import Base
from app.models.test_step import TestStep


# ── SQLAlchemy ORM ──

class TestCase(Base):
    __tablename__ = "test_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    case_name = Column(String, nullable=False)
    case_no = Column(String)
    priority = Column(String, default="P1")
    pre_condition = Column(String)
    steps = Column(String, nullable=False)
    expected_result = Column(String)
    source_excel = Column(String)
    excel_row = Column(Integer)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# ── Pydantic V2 Schema ──

class TestCaseCreate(BaseModel):
    case_name: str = Field(..., min_length=1)
    case_no: Optional[str] = None
    priority: str = Field(default="P1", pattern=r"^P[0-3]$")
    pre_condition: Optional[str] = None
    steps: list[TestStep] = Field(..., min_length=1)
    expected_result: Optional[str] = None
    source_excel: Optional[str] = None
    excel_row: Optional[int] = None


class TestCaseUpdate(BaseModel):
    case_name: Optional[str] = None
    case_no: Optional[str] = None
    priority: Optional[str] = Field(default=None, pattern=r"^P[0-3]$")
    pre_condition: Optional[str] = None
    steps: Optional[list[TestStep]] = None
    expected_result: Optional[str] = None
    status: Optional[str] = None


class TestCaseResponse(BaseModel):
    id: int
    project_id: int
    case_name: str
    case_no: Optional[str]
    priority: str
    pre_condition: Optional[str]
    steps: list[TestStep]
    expected_result: Optional[str]
    source_excel: Optional[str]
    excel_row: Optional[int]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
