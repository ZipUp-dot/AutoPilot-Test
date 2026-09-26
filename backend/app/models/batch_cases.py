"""Batch 逐 Case 历史事实 — ORM 模型 + Pydantic V2 Schema

BatchCase 是「历史事实」的载体：进程重启不丢失。每个 BatchCase 进入 terminal 时
【同事务】写入；terminal 后禁止任何更新（后续 GeneratedCode / validate-on-load
变化不得回写本行。即 is_valid_at_attempt / is_mock_at_attempt / kpi_eligible 等
字段在收口瞬间冻结）。

Schema Delta（待任务 11 Alembic 收口，禁止动 schema.sql）：
  - 新建表 batch_cases：
      id                   INTEGER PK AUTO_INCREMENT
      project_id           INT NOT NULL, FK projects.id ON DELETE CASCADE
      batch_id             VARCHAR(64) NOT NULL
      case_id              INT NOT NULL, FK test_cases.id ON DELETE CASCADE
      status               VARCHAR(20) NOT NULL   -- success / failed / skipped
      code_id              INT NULL, FK generated_codes.id ON DELETE SET NULL
      is_valid_at_attempt  INT NULL   -- 1/0/NULL：首次真实 AI 输出是否过 Validator；
                                       -- mock 或未产生真实输出的 case 为 NULL
      is_mock_at_attempt   INT NOT NULL DEFAULT 0
      error_type           VARCHAR(50) NULL
      attempt_count        INT NOT NULL DEFAULT 0  -- 真实 HTTP attempt 次数
      latency_ms           INT NOT NULL DEFAULT 0
      kpi_eligible         INT NOT NULL DEFAULT 0  -- 是否进入 70% 首生成有效率 cohort
      terminal_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      KEY idx_batch_cases_batch_id (batch_id)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Index, UniqueConstraint
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class BatchCase(Base):
    __tablename__ = "batch_cases"
    __table_args__ = (
        # P0-11 Alembic 收口：UNIQUE(batch_id, case_id)——一个 Batch 中一个 case
        # 只有一条历史事实，防重试/恢复路径产生重复行
        UniqueConstraint("batch_id", "case_id", name="uq_batch_cases_batch_case"),
        Index("idx_batch_cases_batch_id", "batch_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：batch_cases 历史链 FK 均 RESTRICT
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    batch_id = Column(String(64), nullable=False)  # 【不做】FK 到 batch_records（生命周期相反）
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False)
    status = Column(String(20), nullable=False)
    # slot_timeout/circuit_open 等无代码场景 code_id 允许 NULL
    code_id = Column(Integer, ForeignKey("generated_codes.id", ondelete="RESTRICT"))
    is_valid_at_attempt = Column(Integer)
    is_mock_at_attempt = Column(Integer, default=0)
    error_type = Column(String(50))
    attempt_count = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    kpi_eligible = Column(Integer, default=0)
    terminal_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class BatchCaseResponse(BaseModel):
    id: int
    project_id: int
    batch_id: str
    case_id: int
    status: str
    code_id: Optional[int] = None
    is_valid_at_attempt: Optional[bool] = None
    is_mock_at_attempt: bool = False
    error_type: Optional[str] = None
    attempt_count: int = 0
    latency_ms: int = 0
    kpi_eligible: bool = False
    terminal_at: Optional[datetime] = None

    model_config = {"from_attributes": True}