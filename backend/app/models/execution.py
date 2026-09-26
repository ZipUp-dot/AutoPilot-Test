"""执行批次 — ORM 模型 + Pydantic V2 Schema

Schema Delta（P0-9，待任务 11 Alembic 收口，禁止动 schema.sql / 新建 migration）：
  - 新增 stop_requested_at DATETIME NULL：Stop 权威字段（『stop_requested_at 是
    唯一权威』）。queued/running/healing 发起 Stop 时写入；queued 在六步收口事务
    中写入，running/healing 仅写该列（status 保持原值），执行器 loop 结束后由
    ExecutionFinalizer.seal_stopped 收敛。NULL = 无 Stop 意图。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from pydantic import BaseModel, Field

from app.db.database import Base


# ── SQLAlchemy ORM ──

class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史保护：Execution→Project RESTRICT（存在执行记录的项目禁止删除）
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    batch_name = Column(String(255))
    total_cases = Column(Integer, default=0)
    passed_cases = Column(Integer, default=0)
    failed_cases = Column(Integer, default=0)
    status = Column(String(20), default="queued")
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    execution_mode = Column(String(20), default="headless")
    # 执行期持久化字段：Docker 重启后依赖数据库恢复状态
    progress = Column(Integer, default=0)            # 0-100 完成百分比
    worker_id = Column(String(100))                  # 执行 worker 标识（hostname:pid）
    heartbeat_at = Column(DateTime)                  # 最近一次心跳时间
    # P0-6 物化契约：manifest/runtime 快照（Admission 冻结，Manifest immutable）
    manifest_json = Column(Text)                     # Admission Manifest（per-case 快照）
    runtime_state_json = Column(Text)                # {case_id: {"active_code_id": int}}
    # P0-9 Stop 权威字段（Schema Delta）：NULL = 无 Stop 意图；非 NULL = 已请求停止
    stop_requested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())


# ── Pydantic V2 Schema ──

class ExecutionCreate(BaseModel):
    case_ids: list[int] = Field(..., min_length=1)
    batch_name: Optional[str] = None
    execution_mode: str = Field(default="headless")


class ExecutionResponse(BaseModel):
    id: int
    project_id: int
    batch_name: Optional[str]
    total_cases: int
    passed_cases: int
    failed_cases: int
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    execution_mode: str
    progress: int = 0
    worker_id: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
