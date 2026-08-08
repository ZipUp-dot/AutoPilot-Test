"""页面元素 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class PageElement(Base):
    __tablename__ = "page_elements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    element_type = Column(String, nullable=False)
    tag_name = Column(String)
    element_id = Column(String)
    name = Column(String)
    class_name = Column(String)
    selector = Column(String, nullable=False)
    text_content = Column(String)
    placeholder = Column(String)
    is_visible = Column(Integer, default=1)
    bounding_box = Column(String)
    attributes = Column(String)
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
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}
