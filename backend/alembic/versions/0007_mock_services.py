"""Mock 服务 0007：新表 mock_servers + mock_rules

Revision ID: 0007_mock_services
Revises: 0006_pipelines
Create Date: 2026-10-07

PROJ-V20-MOCK（F3）Schema Delta：
  1. 新表 mock_servers —— 逻辑命名空间（非进程实体）：base_path 前缀 + enabled 开关
  2. 新表 mock_rules   —— 路径模板（:param 占位）+ 定制响应

不含 Android 域（不变量 #3；MockServer ≠ AndroidMockDriver，v2.1 §十四）。
零 backfill；只走 Alembic（禁止动 schema.sql）。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_mock_services"
down_revision = "0006_pipelines"
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    # ── 1. mock_servers（逻辑命名空间，非进程实体）──
    op.create_table(
        "mock_servers",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_path", sa.String(length=64), nullable=False,
                  server_default="/mock"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
    )

    # ── 2. mock_rules ──
    op.create_table(
        "mock_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path_pattern", sa.String(length=255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("response_headers", sa.Text(), nullable=True),
        sa.Column("delay_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["server_id"], ["mock_servers.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_mock_rules_server", "mock_rules", ["server_id", "enabled"])


def downgrade() -> None:
    # 直接 drop_table（其索引 / 外键随表一并删除）
    op.drop_table("mock_rules")
    op.drop_table("mock_servers")
