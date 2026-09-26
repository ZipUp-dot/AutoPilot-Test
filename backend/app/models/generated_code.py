"""生成代码 — ORM 模型 + Pydantic V2 Schema

Schema Delta（待任务 11 Alembic 收口，禁止动 schema.sql）：
  - 新增 is_mock INTEGER NOT NULL DEFAULT 0：标记该代码是否为 Mock 模式生成
  - 新增 source_steps_hash VARCHAR(64) NULL：生成时基于的 TestCase.steps 的
    SHA-256；存量行 NULL = 来源不可证明
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Index
from pydantic import BaseModel

from app.db.database import Base


# ── SQLAlchemy ORM ──

class GeneratedCode(Base):
    __tablename__ = "generated_codes"
    __table_args__ = (
        # 与 schema.sql / alembic 0001 索引对齐（保证 autogenerate 零 diff）
        Index("idx_gc_case_id", "case_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：GeneratedCode→TestCase RESTRICT（删除用例时存在生成代码则被 DB 阻断）
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False)
    code_content = Column(Text, nullable=False)
    code_language = Column(String(20), default="python")
    generation_prompt = Column(Text)
    ai_model = Column(String(50))
    is_valid = Column(Integer, default=0)
    syntax_error = Column(Text)
    is_healed = Column(Integer, default=0)
    is_mock = Column(Integer, default=0)
    source_steps_hash = Column(String(64))
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class GeneratedCodeCreate(BaseModel):
    case_id: int
    code_content: str
    code_language: str = "python"
    generation_prompt: Optional[str] = None
    ai_model: Optional[str] = None
    is_valid: int = 0
    is_healed: int = 0


class GeneratedCodeResponse(BaseModel):
    id: int
    case_id: int
    code_content: str
    code_language: str
    generation_prompt: Optional[str]
    ai_model: Optional[str]
    is_valid: int
    syntax_error: Optional[str]
    is_healed: int
    created_at: datetime

    model_config = {"from_attributes": True}
