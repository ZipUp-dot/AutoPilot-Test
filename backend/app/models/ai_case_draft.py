"""AI Case Draft —— AI TestCase 候选草稿（版本化 + 三维独立）

EXT-AITC-10A。三维独立列（8.5：recommended ≠ valid ≠ approved，永不合并）：
  ai_assessment     ∈ recommended / needs_review / not_ready    （AI 自评）
  validation_status ∈ valid / invalid                            （System Validation）
  review_status     ∈ pending / needs_edit / approved / rejected （Human Review）

版本语义（8.6）：同一 draft_key 的多版本共享；needs_edit → 新版本行，
旧行终态冻结（不删除，不变量 #11），旧 approval 自动失效。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (Column, Integer, String, Text, DateTime, ForeignKey,
                        func, Index, UniqueConstraint)
from pydantic import BaseModel

from app.db.database import Base


class AiCaseDraft(Base):
    __tablename__ = "ai_case_drafts"
    __table_args__ = (
        UniqueConstraint("project_id", "draft_key", "draft_version",
                         name="uq_ai_draft_version"),
        Index("idx_draft_review", "project_id", "review_status"),
        Index("idx_draft_snapshot", "snapshot_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"),
                        nullable=False)
    snapshot_id = Column(Integer, ForeignKey("evidence_snapshots.id", ondelete="RESTRICT"),
                         nullable=False)
    draft_key = Column(String(64), nullable=False)
    draft_version = Column(Integer, nullable=False)
    case_name = Column(String(255), nullable=False)
    priority = Column(String(10))
    preconditions = Column(Text)
    steps = Column(Text, nullable=False)
    expected_result = Column(Text)
    ai_assessment = Column(String(20))
    validation_status = Column(String(10))
    validation_errors = Column(Text)
    review_status = Column(String(20), nullable=False, default="pending")
    review_comment = Column(Text)
    promoted_case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="RESTRICT"))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AiCaseDraftResponse(BaseModel):
    id: int
    project_id: int
    snapshot_id: int
    draft_key: str
    draft_version: int
    case_name: str
    priority: Optional[str] = None
    preconditions: Optional[str] = None
    steps: str
    expected_result: Optional[str] = None
    ai_assessment: Optional[str] = None
    validation_status: Optional[str] = None
    validation_errors: Optional[str] = None
    review_status: str
    review_comment: Optional[str] = None
    promoted_case_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
