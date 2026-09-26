"""自愈记录 — ORM 模型 + Pydantic V2 Schema

Schema Delta（P0-8，待任务 11 Alembic 收口，禁止动 schema.sql / 新建 migration）：
  - 新增 execution_id / case_id / round_no（默认 1）/ root_execution_step_id
    （FK→execution_steps，RESTRICT）/ original_code_id / healed_code_id / error_type
  - UNIQUE(execution_id, case_id, round_no) —— DB 级竞态 claim 约束
  既有逐 step 自愈列（execution_step_id 等）保留兼容旧契约；
  新列均 nullable（存量行无值），新 Case 级 HealRound 流程恒写入。
  error_type 允许值七枚（钉死）：heal_finalization_error / validation_error /
  worker_failed / deadline_exceeded / ai_request_failed / ai_schema_error /
  heal_exhausted；success 与 cancelled_by_recovery 时=NULL，failed 时必填。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class HealRecord(Base):
    __tablename__ = "heal_records"
    __table_args__ = (
        # DB 级竞态 claim：同 execution+case 同 round 只允许一条（P0-8）
        UniqueConstraint("execution_id", "case_id", "round_no", name="uq_heal_records_exec_case_round"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：HealRecord→ExecutionStep RESTRICT（步骤是历史事实，禁止被删除）
    execution_step_id = Column(Integer, ForeignKey("execution_steps.id", ondelete="RESTRICT"), nullable=False)
    original_code = Column(Text)
    error_context = Column(Text)
    healed_code = Column(Text)
    heal_prompt = Column(Text)
    retry_status = Column(String(20), default="pending")
    retry_count = Column(Integer, default=0)
    attempts = Column(Text, default="[]")
    created_at = Column(DateTime, default=func.now())

    # ── P0-8 Case 级 HealRound（P0-11 Alembic 已收口）──
    # 历史保护：heal_records 四条历史链 FK 均 RESTRICT
    execution_id = Column(Integer, ForeignKey("executions.id", ondelete="RESTRICT"), nullable=True)
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=True)
    round_no = Column(Integer, default=1, nullable=True)
    # 本 Round 的 root failed step（step_index 最小者）；FK RESTRICT 保护步骤不被删除
    root_execution_step_id = Column(Integer, ForeignKey("execution_steps.id", ondelete="RESTRICT"), nullable=True)
    # 原始代码 / winning candidate（Heal failed/cancelled 时 healed_code_id 允许 NULL）
    original_code_id = Column(Integer, ForeignKey("generated_codes.id", ondelete="RESTRICT"), nullable=True)
    healed_code_id = Column(Integer, ForeignKey("generated_codes.id", ondelete="RESTRICT"), nullable=True)
    error_type = Column(String(50), nullable=True)      # 七值之一；仅 failed 必填


# ── Pydantic V2 Schema ──

class HealRecordCreate(BaseModel):
    execution_step_id: int
    original_code: Optional[str] = None
    error_context: Optional[dict] = None
    healed_code: Optional[str] = None
    heal_prompt: Optional[str] = None
    retry_count: int = 0


class HealRecordResponse(BaseModel):
    id: int
    execution_step_id: int
    original_code: Optional[str]
    error_context: Optional[dict]
    healed_code: Optional[str]
    heal_prompt: Optional[str]
    retry_status: str
    retry_count: int
    created_at: datetime

    model_config = {"from_attributes": True}
