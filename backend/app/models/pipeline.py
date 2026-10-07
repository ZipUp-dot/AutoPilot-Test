"""流水线 — ORM 模型 + Pydantic V2 Schema

PROJ-V20-CICD（F2）。
  - pipelines：流水线定义（trigger_config_json / stages_json / enabled）；
  - pipeline_runs：一次触发实例；status 为**派生态**（由下属 executions 终态汇总，
    无独立状态机，唯一 writer = PipelineService 汇总函数）。

trigger_config_json：{"manual": bool, "schedule_id": int|null, "webhook_token": str|null}
stages_json：[{"name": str, "case_selector": {"case_ids": [int]}, "env": str|null}]

Schema 与 ORM 同文件（仓库 models 包既定约定）。
"""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import (Column, Integer, String, Text, DateTime, Boolean,
                        ForeignKey, func, Index)
from pydantic import BaseModel, Field, field_validator

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Pipeline(Base):
    __tablename__ = "pipelines"
    __table_args__ = (
        Index("idx_pipe_project", "project_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"),
                        nullable=False)
    name = Column(String(128), nullable=False)
    trigger_config_json = Column(Text, nullable=False)
    stages_json = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("idx_prun_pipeline", "pipeline_id", "started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(Integer, ForeignKey("pipelines.id", ondelete="RESTRICT"),
                         nullable=False)
    trigger_type = Column(String(20), nullable=False)   # schedule / manual / webhook
    trigger_detail = Column(Text, nullable=True)
    # 派生态：queued / running / success / failed（由 executions 汇总，非状态机）
    status = Column(String(20), nullable=False, default="queued")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


# ── Pydantic V2 Schema ──

def _parse_json_field(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return {} if not v.startswith("[") else []
    return v


class PipelineCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    trigger_config_json: dict = Field(default_factory=dict)
    stages_json: list[dict] = Field(..., min_length=1)
    enabled: bool = True


class PipelineUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    trigger_config_json: Optional[dict] = None
    stages_json: Optional[list[dict]] = Field(default=None, min_length=1)
    enabled: Optional[bool] = None


class PipelineResponse(BaseModel):
    id: int
    project_id: int
    name: str
    trigger_config_json: dict
    stages_json: list
    enabled: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("trigger_config_json", mode="before")
    @classmethod
    def _v_cfg(cls, v):
        parsed = _parse_json_field(v)
        return parsed if isinstance(parsed, dict) else {}

    @field_validator("stages_json", mode="before")
    @classmethod
    def _v_stages(cls, v):
        parsed = _parse_json_field(v)
        return parsed if isinstance(parsed, list) else []


class PipelineRunResponse(BaseModel):
    id: int
    pipeline_id: int
    trigger_type: str
    trigger_detail: Optional[dict] = None
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("trigger_detail", mode="before")
    @classmethod
    def _v_detail(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return v
