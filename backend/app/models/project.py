"""项目 — ORM 模型 + Pydantic V2 Schema"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, func
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    target_url = Column(String, nullable=False)
    test_path = Column(String, default="/")
    browser_type = Column(String, default="chromium")
    headless = Column(Integer, default=1)
    status = Column(String, default="active")
    platform = Column(String, default="web", nullable=False)
    config_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# ── Pydantic V2 Schema ──

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1)
    target_url: str = Field(..., min_length=1)
    test_path: str = Field(default="/")
    browser_type: str = Field(default="chromium")
    headless: int = Field(default=1, ge=0, le=1)
    platform: str = Field(default="web", pattern="^(web|android)$")
    config_json: Optional[dict] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    target_url: Optional[str] = None
    test_path: Optional[str] = None
    browser_type: Optional[str] = None
    headless: Optional[int] = Field(default=None, ge=0, le=1)
    status: Optional[str] = None
    config_json: Optional[dict] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    target_url: str
    test_path: str
    browser_type: str
    headless: int
    platform: str
    config_json: Optional[dict] = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
