"""可视化 CI/CD 0006：新表 pipelines + pipeline_runs + executions.pipeline_run_id

Revision ID: 0006_pipelines
Revises: 0005_schedules
Create Date: 2026-10-07

PROJ-V20-CICD（F2）Schema Delta：
  1. 新表 pipelines      —— 流水线定义（触发配置 + 阶段定义）
  2. 新表 pipeline_runs  —— 一次触发实例（status 为派生态，由 executions 汇总）
  3. executions += pipeline_run_id (FK → pipeline_runs.id, RESTRICT, nullable)
     历史行为 NULL = 非流水线触发（不回填）。

Owner 裁定（2026-10-07）：§6「汇总挂在 execution Seal 后的既有钩子点」要求
execution→run 可定位，而 §3 列清单无该字段 —— 裁定以 executions.pipeline_run_id
承载链接（§10「两表 + 索引」字面扩 1 列，Owner 追认）。

零 backfill；只走 Alembic（禁止动 schema.sql）。
SQLite 兼容：executions 加列含 FK，必须走 batch_alter_table（表重建）。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_pipelines"
down_revision = "0005_schedules"
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    # ── 1. pipelines ──
    op.create_table(
        "pipelines",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("trigger_config_json", sa.Text(), nullable=False),
        sa.Column("stages_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_pipe_project", "pipelines", ["project_id"])

    # ── 2. pipeline_runs ──
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("pipeline_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("trigger_detail", sa.Text(), nullable=True),
        # 派生态（由下属 executions 终态汇总；无独立状态机）
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="queued"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_prun_pipeline", "pipeline_runs", ["pipeline_id", "started_at"])

    # ── 3. executions += pipeline_run_id（batch 重建以兼容 SQLite 的 FK 加列）──
    with op.batch_alter_table("executions") as batch_op:
        batch_op.add_column(sa.Column("pipeline_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_executions_pipeline_run", "pipeline_runs",
            ["pipeline_run_id"], ["id"], ondelete="RESTRICT",
        )
    op.create_index("idx_exec_pipeline_run", "executions", ["pipeline_run_id"])


def downgrade() -> None:
    # MySQL 陷阱（ERR 1553）：idx_exec_pipeline_run 可能被 FK 约束占用，
    # 必须先删约束，再删索引，最后删列（禁止先单独 drop_index）。
    with op.batch_alter_table("executions") as batch_op:
        batch_op.drop_constraint("fk_executions_pipeline_run", type_="foreignkey")
    op.drop_index("idx_exec_pipeline_run", table_name="executions")
    with op.batch_alter_table("executions") as batch_op:
        batch_op.drop_column("pipeline_run_id")

    # 直接 drop_table（其索引 / 外键随表一并删除）
    op.drop_table("pipeline_runs")
    op.drop_table("pipelines")
