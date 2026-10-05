"""Batch 运行态元信息表 batch_jobs — 服务重启续跑（resume）的持久化载体

Revision ID: 0003_batch_jobs
Revises: 0002_formal_delta
Create Date: 2026-10-05

新增 batch_jobs 表，存储每个 Batch 的原始请求 case 集合 + 运行态 status：
  - 创建 job 时写 status=running；
  - finalize 时更新为 completed/failed 并写 terminal_at；
  - 应用启动时扫描 status=running 的 open job，重建内存 BatchJob 并续跑未终态用例，
    解决「后端重启即丢在途批量生成」的问题。

历史保护：batch_jobs.project_id → projects.id RESTRICT（与 batch_cases / batch_records 一致）。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_batch_jobs"
down_revision = "0002_formal_delta"
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "batch_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("case_ids", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.Column("updated_at", sa.DateTime(), server_default=_TS),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("batch_id", name="uq_batch_jobs_batch_id"),
    )


def downgrade() -> None:
    op.drop_table("batch_jobs")