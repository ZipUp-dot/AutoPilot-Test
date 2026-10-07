"""定时执行 0005：新表 schedules

Revision ID: 0005_schedules
Revises: 0004_ai_case_drafts
Create Date: 2026-10-07

PROJ-V20-SCHED（F1）Schema Delta：
  新表 schedules —— 定时任务配置（cron + 执行配置）+ 调度回填字段。
  stop_requested_at 为 Schedule Stop 唯一权威（"停止调度"写入），与
  executions.stop_requested_at（Execution Stop 权威）语义分离：
  两者属不同实体，互不干扰（不变量 #6 不冲突）。

零 backfill；只走 Alembic（禁止动 schema.sql）。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_schedules"
down_revision = "0004_ai_case_drafts"
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("cron_expr", sa.String(length=64), nullable=False),
        sa.Column("exec_config_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_execution_id", sa.Integer(), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.Column("updated_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["last_execution_id"], ["executions.id"],
                                ondelete="RESTRICT"),
    )
    op.create_index("idx_sched_project", "schedules", ["project_id", "enabled"])
    op.create_index("idx_sched_next", "schedules", ["next_run_at", "enabled"])


def downgrade() -> None:
    # 直接 drop_table（其索引 / 外键随表一并删除）。
    op.drop_table("schedules")
