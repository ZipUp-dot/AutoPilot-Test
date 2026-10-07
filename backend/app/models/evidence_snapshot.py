"""Evidence Snapshot —— 抓取证据固化（EXT-AITC-10A）

域限定命名：evidence_snapshot_* —— 避免与 Frozen Spec 的 Manifest `*_snapshot`
冻结字段撞名（C-17 裁定：跨层术语必须带域限定符）。

语义：一次抓取 = 一条 snapshot（snapshot_hash 为本次元素集规范化后的 SHA256），
元素行通过 page_elements.snapshot_id 归属该次抓取。历史行 snapshot_id=NULL = legacy。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import validates
from pydantic import BaseModel

from app.db.database import Base


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"
    __table_args__ = (
        Index("idx_snap_project", "project_id", "crawl_timestamp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"),
                        nullable=False)
    source_url = Column(String(512), nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    element_count = Column(Integer, nullable=False)
    crawl_timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=func.now())

    @validates("crawl_timestamp")
    def _coerce_crawl_timestamp(self, key, value):
        """容忍 ISO-8601 字符串（外部/测试来源），统一落 datetime。

        SQLite 的 DateTime 绑定只接受 datetime/date 对象（TypeError 拒绝字符串），
        而 MySQL 接受字符串 —— 在 ORM 边界统一收口，保证双方言一致；
        存储列仍为 DATETIME（DDL 不变），不产生 autogenerate diff。
        """
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        return value


class EvidenceSnapshotResponse(BaseModel):
    id: int
    project_id: int
    source_url: str
    snapshot_hash: str
    element_count: int
    crawl_timestamp: datetime
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
