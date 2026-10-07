"""AI TestCase 链 0004：evidence_snapshots + ai_case_drafts + 2 处改列

Revision ID: 0004_ai_case_drafts
Revises: 0003_batch_jobs
Create Date: 2026-10-07

EXT-AITC-10A（F1）Schema Delta：
  1. 新表 evidence_snapshots —— 抓取证据固化（回答"Draft 基于哪次抓取"）
  2. 新表 ai_case_drafts     —— Draft 版本化持久化 + 三维独立列
  3. page_elements += snapshot_id (FK → evidence_snapshots, RESTRICT, nullable)
     历史行为 NULL = legacy（不回填；不变量 #11 历史事实不 hard delete）
  4. test_cases    += source (NOT NULL DEFAULT 'excel', CHECK ∈ {excel, ai_draft})
     仅 provenance，不进 StepCanonicalizer / source_steps_hash（8.8）

建表顺序（Owner 提醒 1 · 循环依赖）：
  evidence_snapshots → ai_case_drafts
  （ai_case_drafts.promoted_case_id → test_cases 时 test_cases 已存在，无循环）

SQLite 兼容：改列涉及 FK / CHECK 约束，必须走 batch_alter_table（表重建），
裸 ADD COLUMN 在 SQLite 上会丢弃约束。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_ai_case_drafts"
down_revision = "0003_batch_jobs"
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    # ── 1. evidence_snapshots ──
    op.create_table(
        "evidence_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("element_count", sa.Integer(), nullable=False),
        sa.Column("crawl_timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_snap_project", "evidence_snapshots",
                    ["project_id", "crawl_timestamp"])

    # ── 2. ai_case_drafts ──
    op.create_table(
        "ai_case_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("draft_key", sa.String(length=64), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("case_name", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=True),
        sa.Column("preconditions", sa.Text(), nullable=True),
        sa.Column("steps", sa.Text(), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=True),
        # 三维独立列（8.5：recommended ≠ valid ≠ approved，永不合并）
        sa.Column("ai_assessment", sa.String(length=20), nullable=True),
        sa.Column("validation_status", sa.String(length=10), nullable=True),
        sa.Column("validation_errors", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False,
                  server_default="pending"),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("promoted_case_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.Column("updated_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["evidence_snapshots.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promoted_case_id"], ["test_cases.id"],
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "draft_key", "draft_version",
                            name="uq_ai_draft_version"),
    )
    op.create_index("idx_draft_review", "ai_case_drafts", ["project_id", "review_status"])
    op.create_index("idx_draft_snapshot", "ai_case_drafts", ["snapshot_id"])

    # ── 3. page_elements += snapshot_id（FK，需 batch 重建以兼容 SQLite）──
    with op.batch_alter_table("page_elements") as batch_op:
        batch_op.add_column(sa.Column("snapshot_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_page_elements_snapshot", "evidence_snapshots",
            ["snapshot_id"], ["id"], ondelete="RESTRICT",
        )

    # ── 4. test_cases += source（NOT NULL DEFAULT 'excel' + CHECK，需 batch）──
    with op.batch_alter_table("test_cases") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=16),
                                      nullable=False, server_default="excel"))
        batch_op.create_check_constraint(
            "ck_test_cases_source", "source IN ('excel', 'ai_draft')"
        )


def downgrade() -> None:
    with op.batch_alter_table("test_cases") as batch_op:
        batch_op.drop_constraint("ck_test_cases_source", type_="check")
        batch_op.drop_column("source")

    with op.batch_alter_table("page_elements") as batch_op:
        batch_op.drop_constraint("fk_page_elements_snapshot", type_="foreignkey")
        batch_op.drop_column("snapshot_id")

    # 直接 drop_table（其索引 / 外键随表一并删除）。
    # 禁止先单独 drop_index：MySQL 下 idx_draft_snapshot / idx_snap_project 承载
    # FK 约束，ERR 1553 "Cannot drop index ... needed in a foreign key constraint"。
    op.drop_table("ai_case_drafts")
    op.drop_table("evidence_snapshots")
