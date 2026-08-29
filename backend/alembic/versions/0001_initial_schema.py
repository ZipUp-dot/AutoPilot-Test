"""initial schema — AutoPilot 全量建表（MySQL / SQLite 双方言）

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-29

设计说明：
- 列定义与 app/db/schema.sql 及 ORM models 对齐，MySQL / SQLite 通用类型。
- 平滑迁移：online 模式下若表已存在（schema.sql / create_all 已建），跳过建表，
  保证已有数据库 `alembic upgrade head` 不报错；offline 模式（--sql）不查连接，
  总是生成完整 CREATE TABLE 供空库执行。
- TEXT 列不设 server_default（MySQL 8.0 不允许 TEXT 默认值；ORM Python 层负责默认）。
- updated_at 仅 server_default=CURRENT_TIMESTAMP，无 ON UPDATE（SQLite 不支持，
  更新由 ORM onupdate=func.now() 负责）。

"""

from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")


def _table_exists(name: str) -> bool:
    """online 模式检查表是否存在；offline 模式（--sql，连接为 MockConnection）返回 False"""
    if context.is_offline_mode():
        return False
    conn = context.get_context().connection
    if conn is None:
        return False
    return sa.inspect(conn).has_table(name)


def _create_projects() -> None:
    if _table_exists("projects"):
        return
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_url", sa.String(length=500), nullable=False),
        sa.Column("test_path", sa.String(length=255), server_default="/"),
        sa.Column("browser_type", sa.String(length=20), server_default="chromium"),
        sa.Column("headless", sa.Integer(), server_default="1"),
        sa.Column("status", sa.String(length=20), server_default="active"),
        sa.Column("platform", sa.String(length=10), nullable=False, server_default="web"),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.Column("updated_at", sa.DateTime(), server_default=_TS),
    )


def _create_page_elements() -> None:
    if _table_exists("page_elements"):
        return
    op.create_table(
        "page_elements",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("element_type", sa.String(length=50), nullable=False),
        sa.Column("tag_name", sa.String(length=50), nullable=True),
        sa.Column("element_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("class_name", sa.String(length=500), nullable=True),
        sa.Column("selector", sa.String(length=500), nullable=False),
        sa.Column("text_content", sa.String(length=500), nullable=True),
        sa.Column("placeholder", sa.String(length=255), nullable=True),
        sa.Column("is_visible", sa.Integer(), server_default="1"),
        sa.Column("bounding_box", sa.Text(), nullable=True),
        sa.Column("attributes", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=10), nullable=False, server_default="web"),
        sa.Column("selector_type", sa.String(length=20), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_pe_project_id", "page_elements", ["project_id"])
    op.create_index("idx_pe_element_type", "page_elements", ["element_type"])


def _create_test_cases() -> None:
    if _table_exists("test_cases"):
        return
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("case_name", sa.String(length=255), nullable=False),
        sa.Column("case_no", sa.String(length=50), nullable=True),
        sa.Column("priority", sa.String(length=10), server_default="P1"),
        sa.Column("pre_condition", sa.Text(), nullable=True),
        sa.Column("steps", sa.Text(), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("source_excel", sa.String(length=255), nullable=True),
        sa.Column("excel_row", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.Column("updated_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_tc_project_id", "test_cases", ["project_id"])
    op.create_index("idx_tc_status", "test_cases", ["status"])


def _create_generated_codes() -> None:
    if _table_exists("generated_codes"):
        return
    op.create_table(
        "generated_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("code_content", sa.Text(), nullable=False),
        sa.Column("code_language", sa.String(length=20), server_default="python"),
        sa.Column("generation_prompt", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.String(length=50), nullable=True),
        sa.Column("is_valid", sa.Integer(), server_default="0"),
        sa.Column("syntax_error", sa.Text(), nullable=True),
        sa.Column("is_healed", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_gc_case_id", "generated_codes", ["case_id"])


def _create_executions() -> None:
    if _table_exists("executions"):
        return
    op.create_table(
        "executions",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("batch_name", sa.String(length=255), nullable=True),
        sa.Column("total_cases", sa.Integer(), server_default="0"),
        sa.Column("passed_cases", sa.Integer(), server_default="0"),
        sa.Column("failed_cases", sa.Integer(), server_default="0"),
        sa.Column("status", sa.String(length=20), server_default="queued"),
        sa.Column("start_time", sa.DateTime(), nullable=True),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("execution_mode", sa.String(length=20), server_default="headless"),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )


def _create_execution_steps() -> None:
    if _table_exists("execution_steps"):
        return
    op.create_table(
        "execution_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("execution_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=True),
        sa.Column("target_selector", sa.String(length=500), nullable=True),
        sa.Column("input_value", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending"),
        sa.Column("screenshot_before", sa.String(length=500), nullable=True),
        sa.Column("screenshot_after", sa.String(length=500), nullable=True),
        sa.Column("log_output", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("exception_type", sa.String(length=100), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_es_execution_id", "execution_steps", ["execution_id"])
    op.create_index("idx_es_case_id", "execution_steps", ["case_id"])


def _create_execution_reports() -> None:
    if _table_exists("execution_reports"):
        return
    op.create_table(
        "execution_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("execution_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("report_html", sa.Text(), nullable=True),
        sa.Column("report_summary", sa.Text(), nullable=True),
        sa.Column("download_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
    )


def _create_heal_records() -> None:
    if _table_exists("heal_records"):
        return
    op.create_table(
        "heal_records",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("execution_step_id", sa.Integer(), nullable=False),
        sa.Column("original_code", sa.Text(), nullable=True),
        sa.Column("error_context", sa.Text(), nullable=True),
        sa.Column("healed_code", sa.Text(), nullable=True),
        sa.Column("heal_prompt", sa.Text(), nullable=True),
        sa.Column("retry_status", sa.String(length=20), server_default="pending"),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        # attempts 无 server_default：MySQL 8.0 不允许 TEXT 默认值
        sa.Column("attempts", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(
            ["execution_step_id"], ["execution_steps.id"], ondelete="CASCADE"
        ),
    )


_TABLE_CREATORS = [
    _create_projects,
    _create_page_elements,
    _create_test_cases,
    _create_generated_codes,
    _create_executions,
    _create_execution_steps,
    _create_execution_reports,
    _create_heal_records,
]


def upgrade() -> None:
    for creator in _TABLE_CREATORS:
        creator()


def downgrade() -> None:
    """回滚：按依赖逆序删除表（已存在才删除）"""
    for table in (
        "heal_records",
        "execution_reports",
        "execution_steps",
        "executions",
        "generated_codes",
        "test_cases",
        "page_elements",
        "projects",
    ):
        if _table_exists(table):
            op.drop_table(table)
