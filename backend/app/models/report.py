"""执行报告 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Report(Base):
    __tablename__ = "execution_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("executions.id", ondelete="CASCADE"), unique=True, nullable=False)
    report_html = Column(String)
    report_summary = Column(String)
    download_url = Column(String)
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class ReportCreate(BaseModel):
    execution_id: int
    report_html: Optional[str] = None
    report_summary: Optional[dict] = None
    download_url: Optional[str] = None


class ReportResponse(BaseModel):
    id: int
    execution_id: int
    report_html: Optional[str]
    report_summary: Optional[dict]
    download_url: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
