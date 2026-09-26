"""执行报告 — ORM 模型 + Pydantic V2 Schema

Schema Delta（P0-10，待任务 11 Alembic 收口，禁止动 schema.sql / 新建 migration）：
  - execution_id 由 unique=True 改为普通 FK，UNIQUE 收敛为 (execution_id, report_type)
    复合唯一约束 uq_execution_reports_execution_type——同一 Execution 只生成与其
    终态对应的那一个 report_type（full/partial/diagnostic/interrupted）。
  - 新增 report_type VARCHAR(20)：报告终态分型，映射钉死
    completed→full、stopped→partial、failed→diagnostic、interrupted→interrupted；
    非终态 status（queued/running/healing）兜底 full。禁止自定义值。
  - 新增 generation_status VARCHAR(20)：报告生成状态机
    generating→ready/failed、failed→generating；不存在 pending 态（claim 即创建
    generating）。ready=直接复用；generating 未超时=拒绝；generating 已超时=reclaim。
  - 新增 claim_token VARCHAR(64)：owner fencing 唯一凭据。每次 claim/reclaim 生成新
    token，旧 token 立即失效；生成完成/失败写回必须携带本次 token 走 fenced UPDATE
    （WHERE execution_id AND report_type AND generation_status='generating' AND
    claim_token=<本次 token>），旧 owner 晚到写回影响行数=0，不得覆盖新 owner 结果。
  - 新增 claimed_at DATETIME NULL：claim 时间戳，超时判定
    （REPORT_CLAIM_TIMEOUT_SECONDS）依据。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Report(Base):
    __tablename__ = "execution_reports"
    __table_args__ = (
        UniqueConstraint("execution_id", "report_type",
                         name="uq_execution_reports_execution_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：ExecutionReport→Execution RESTRICT
    execution_id = Column(Integer, ForeignKey("executions.id", ondelete="RESTRICT"), nullable=False)
    # P0-10：终态分型（full/partial/diagnostic/interrupted，映射钉死）+ claim 状态机字段
    report_type = Column(String(20), nullable=False, default="full")
    generation_status = Column(String(20), nullable=False, default="generating")
    claim_token = Column(String(64), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    report_html = Column(Text)
    report_summary = Column(Text)
    download_url = Column(String(500))
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
