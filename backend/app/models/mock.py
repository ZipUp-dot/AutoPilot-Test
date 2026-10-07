"""Mock 服务 — ORM 模型 + Pydantic V2 Schema

PROJ-V20-MOCK（F3）。MockServer 是**逻辑命名空间**（非进程实体，不占端口不起进程）：
执行期由 Playwright context.route 拦截 base_path 前缀的请求。
严格 Web 域：不涉及 Android 驱动 Mock（AndroidMockDriver 属 Android 域，不变量 #3）。

触发引用：Project.config_json["mock_server_id"]（可选，可空）→ Admission 冻结进
Manifest → 执行期 playwright_service 依快照注册拦截。
"""

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (Column, Integer, String, Text, DateTime, Boolean,
                        ForeignKey, func, Index)
from pydantic import BaseModel, Field, field_validator

from app.db.database import Base

#: §3 限定的 HTTP 方法集合
ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE")


# ── SQLAlchemy ORM ──

class MockServer(Base):
    __tablename__ = "mock_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"),
                        nullable=False)
    name = Column(String(128), nullable=False)
    base_path = Column(String(64), nullable=False, default="/mock")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=func.now())


class MockRule(Base):
    __tablename__ = "mock_rules"
    __table_args__ = (
        Index("idx_mock_rules_server", "server_id", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("mock_servers.id", ondelete="RESTRICT"),
                       nullable=False)
    method = Column(String(8), nullable=False)
    path_pattern = Column(String(255), nullable=False)
    status_code = Column(Integer, nullable=False, default=200)
    response_body = Column(Text, nullable=False)
    response_headers = Column(Text, nullable=True)
    delay_ms = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

def _parse_json(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return default
    return v if v is not None else default


def _validate_pattern(v: Optional[str]) -> Optional[str]:
    if v is not None:
        # 局部导入：避免 models → services 模块级循环依赖
        from app.services.mock_service import validate_path_pattern
        validate_path_pattern(v)
    return v


class MockServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    base_path: str = Field(default="/mock", max_length=64)
    enabled: bool = True


class MockServerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    base_path: Optional[str] = Field(default=None, max_length=64)
    enabled: Optional[bool] = None


class MockServerResponse(BaseModel):
    id: int
    project_id: int
    name: str
    base_path: str
    enabled: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MockRuleCreate(BaseModel):
    method: str = Field(..., pattern="^(GET|POST|PUT|DELETE)$")
    path_pattern: str = Field(..., max_length=255)
    status_code: int = Field(default=200, ge=100, le=599)
    response_body: Any = Field(default_factory=dict)
    response_headers: Optional[dict] = None
    delay_ms: int = Field(default=0, ge=0)
    enabled: bool = True

    @field_validator("path_pattern")
    @classmethod
    def _v_pattern(cls, v: str) -> str:
        return _validate_pattern(v)


class MockRuleUpdate(BaseModel):
    method: Optional[str] = Field(default=None, pattern="^(GET|POST|PUT|DELETE)$")
    path_pattern: Optional[str] = Field(default=None, max_length=255)
    status_code: Optional[int] = Field(default=None, ge=100, le=599)
    response_body: Optional[Any] = None
    response_headers: Optional[dict] = None
    delay_ms: Optional[int] = Field(default=None, ge=0)
    enabled: Optional[bool] = None

    @field_validator("path_pattern")
    @classmethod
    def _v_pattern(cls, v: Optional[str]) -> Optional[str]:
        return _validate_pattern(v)


class MockRuleResponse(BaseModel):
    id: int
    server_id: int
    method: str
    path_pattern: str
    status_code: int
    response_body: Any = None
    response_headers: Optional[dict] = None
    delay_ms: int
    enabled: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("response_body", mode="before")
    @classmethod
    def _v_body(cls, v):
        return _parse_json(v, None)

    @field_validator("response_headers", mode="before")
    @classmethod
    def _v_headers(cls, v):
        return _parse_json(v, None)
