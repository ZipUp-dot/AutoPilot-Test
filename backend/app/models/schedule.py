"""定时任务 — ORM 模型 + Pydantic V2 Schema

PROJ-V20-SCHED（F1）。schedules 表：cron 表达式 + 执行配置 + 调度回填字段。

stop_requested_at 为 Schedule Stop 唯一权威（"停止调度"写入），与
executions.stop_requested_at（Execution Stop 权威）语义分离——两者属不同实体，
disable 不杀运行中 Execution（Spec §4 / AC-03）。

Schema 与 ORM 同文件（仓库 models 包既定约定：见 app/models/__init__.py 头部）。
"""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import (Column, Integer, String, Text, DateTime, Boolean,
                        ForeignKey, func, Index)
from pydantic import BaseModel, Field, field_validator

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("idx_sched_project", "project_id", "enabled"),
        Index("idx_sched_next", "next_run_at", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：Schedule→Project RESTRICT
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"),
                        nullable=False)
    name = Column(String(128), nullable=False)
    cron_expr = Column(String(64), nullable=False)
    exec_config_json = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_execution_id = Column(Integer,
                               ForeignKey("executions.id", ondelete="RESTRICT"),
                               nullable=True)
    # Schedule Stop 唯一权威（与 executions.stop_requested_at 分离）
    stop_requested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# ── Pydantic V2 Schema ──

def _validate_cron_field(v: Optional[str]) -> Optional[str]:
    """cron 合法性校验（5 段标准 cron）；非法抛 ValueError → API 422"""
    if v is not None:
        # 局部导入：避免 models → services 的模块级循环依赖
        from app.services.scheduler_service import validate_cron
        validate_cron(v)
    return v


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    cron_expr: str = Field(..., max_length=64)
    exec_config_json: dict

    @field_validator("cron_expr")
    @classmethod
    def _check_cron(cls, v: str) -> str:
        return _validate_cron_field(v)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    cron_expr: Optional[str] = Field(default=None, max_length=64)
    exec_config_json: Optional[dict] = None
    enabled: Optional[bool] = None

    @field_validator("cron_expr")
    @classmethod
    def _check_cron(cls, v: Optional[str]) -> Optional[str]:
        return _validate_cron_field(v)


class ScheduleResponse(BaseModel):
    id: int
    project_id: int
    name: str
    cron_expr: str
    exec_config_json: dict
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_execution_id: Optional[int] = None
    stop_requested_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("exec_config_json", mode="before")
    @classmethod
    def _parse_exec_config(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return v or {}
