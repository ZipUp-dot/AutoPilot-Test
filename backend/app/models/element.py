"""页面元素 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Index
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class PageElement(Base):
    __tablename__ = "page_elements"
    __table_args__ = (
        # 与 schema.sql / alembic 0001 索引对齐（保证 autogenerate 零 diff）
        Index("idx_pe_project_id", "project_id"),
        Index("idx_pe_element_type", "element_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    element_type = Column(String(50), nullable=False)
    tag_name = Column(String(50))
    element_id = Column(String(255))
    name = Column(String(255))
    class_name = Column(String(500))
    selector = Column(String(500), nullable=False)
    text_content = Column(String(500))
    placeholder = Column(String(255))
    is_visible = Column(Integer, default=1)
    bounding_box = Column(Text)
    attributes = Column(Text)
    platform = Column(String(10), default="web", nullable=False)
    selector_type = Column(String(20), nullable=True)
    element_metadata = Column("metadata", Text, nullable=True)
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class PageElementCreate(BaseModel):
    element_type: str = Field(..., description="button/input/link/select/textarea 等")
    tag_name: Optional[str] = None
    element_id: Optional[str] = None
    name: Optional[str] = None
    class_name: Optional[str] = None
    selector: str = Field(..., min_length=1)
    text_content: Optional[str] = None
    placeholder: Optional[str] = None
    is_visible: int = Field(default=1, ge=0, le=1)
    bounding_box: Optional[dict] = None
    attributes: Optional[dict] = None
    platform: str = Field(default="web", pattern="^(web|android)$")
    selector_type: Optional[str] = None
    element_metadata: Optional[dict] = Field(default=None, alias="metadata")

    model_config = {"populate_by_name": True}


class PageElementUpdate(BaseModel):
    element_type: Optional[str] = None
    tag_name: Optional[str] = None
    element_id: Optional[str] = None
    name: Optional[str] = None
    class_name: Optional[str] = None
    selector: Optional[str] = None
    text_content: Optional[str] = None
    placeholder: Optional[str] = None
    is_visible: Optional[int] = Field(default=None, ge=0, le=1)
    bounding_box: Optional[dict] = None
    attributes: Optional[dict] = None
    selector_type: Optional[str] = None
    element_metadata: Optional[dict] = Field(default=None, alias="metadata")

    model_config = {"populate_by_name": True}


class PageElementResponse(BaseModel):
    id: int
    project_id: int
    element_type: str
    tag_name: Optional[str]
    element_id: Optional[str]
    name: Optional[str]
    class_name: Optional[str]
    selector: str
    text_content: Optional[str]
    placeholder: Optional[str]
    is_visible: int
    bounding_box: Optional[dict]
    attributes: Optional[dict]
    platform: str = "web"
    selector_type: Optional[str] = None
    element_metadata: Optional[dict] = Field(default=None, alias="metadata")
    created_at: Optional[datetime]

    model_config = {"from_attributes": True, "populate_by_name": True}
