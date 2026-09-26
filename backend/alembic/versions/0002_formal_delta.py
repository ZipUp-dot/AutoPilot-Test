"""P0-11 formal delta — Alembic 单一 Schema 权威收口（历史保护 + 终态分型 + Batch 事实表）

Revision ID: 0002_formal_delta
Revises: 0001_initial_schema
Create Date: 2026-09-26

本 revision 承载 Frozen Spec 的全部累积 Schema Delta（对照 app/models/ 逐项核对）：
  1. generated_codes  += is_mock INTEGER NOT NULL DEFAULT 0 / source_steps_hash VARCHAR(64)
  2. executions        += manifest_json TEXT / runtime_state_json TEXT / stop_requested_at DATETIME
  3. execution_steps   += assertion TEXT / skip_reason VARCHAR(50) / error_type VARCHAR(50)
                          + UNIQUE(execution_id, case_id, step_index)
  4. heal_records      += execution_id / case_id / round_no / root_execution_step_id /
                          original_code_id / healed_code_id / error_type
                          + UNIQUE(execution_id, case_id, round_no)
  5. execution_reports 由 execution_id 单列 UNIQUE → UNIQUE(execution_id, report_type)，
                          += report_type / generation_status / claim_token / claimed_at
  6. 新建 batch_cases（UNIQUE(batch_id, case_id) 防重试/恢复重复行，INDEX(batch_id)）
  7. 新建 batch_records（batch_status + summary_json + UNIQUE(batch_id)）

历史保护：历史事实链 FK 由 CASCADE 改为 RESTRICT（删除父对象由 DB 层阻断）：
  - GeneratedCode→TestCase、Execution→Project、ExecutionStep→TestCase/Execution、
    ExecutionReport→Execution、HealRecord→ExecutionStep、BatchRecord→Project、
    heal_records.execution_id→executions / case_id→test_cases / original_code_id→generated_codes
    / healed_code_id→generated_codes（可 NULL）、batch_cases.project_id→Project / case_id→TestCase
    / code_id→GeneratedCode（可 NULL）
  - batch_cases.batch_id 【不做】FK 到 batch_records（生命周期相反：BatchCase 先生成，
    BatchRecord 收口时才创建）

SQLite 走 table rebuild（batch 模式 + naming_convention 重命名未命名约束），
MySQL 走 constraint migration（反射真实约束名后 drop/recreate）。
迁移预检（孤儿行 + UNIQUE 冲突）在 upgrade() 开头执行，不通过则抛错拒绝完成。
"""

from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_formal_delta"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

_TS = sa.text("CURRENT_TIMESTAMP")

# batch 模式命名约定：SQLite 重建表时把未命名约束重命名为确定性名字，便于 drop
_NC = {
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
}


def _conv_fk(table: str, col: str, ref_table: str) -> str:
    return "fk_%s_%s_%s" % (table, col, ref_table)


def _conv_uq(table: str, *cols: str) -> str:
    return "uq_%s_%s" % (table, "_".join(cols))


def _preflight(conn) -> None:
    """迁移预检：扫描孤儿行与 UNIQUE 冲突；有问题则抛错并打印清单（拒绝完成）。"""
    if context.is_offline_mode():
        return
    from app.db.migration_preflight import run_preflight

    issues = run_preflight(conn)
    if issues:
        detail = "\n".join(issues)
        raise RuntimeError(
            "迁移预检未通过，拒绝完成（%d 项问题）：\n%s" % (len(issues), detail)
        )


def _fk_name(table: str, col: str, ref_table: str) -> str | None:
    """返回 (table, col)→ref_table 外键的反射名；MySQL 旧库可能为 table_ibfk_N。"""
    if context.is_offline_mode():
        return None
    insp = sa.inspect(op.get_bind())
    for fk in insp.get_foreign_keys(table):
        if fk["constrained_columns"] == [col] and fk["referred_table"] == ref_table:
            return fk["name"] or _conv_fk(table, col, ref_table)
    return None


def _set_fk_restrict(table: str, col: str, ref_table: str, ref_cols) -> None:
    """把 (table, col)→ref_table 的外键 ondelete 改为 RESTRICT（幂等：已是则跳过）。

    SQLite：batch 重建表。MySQL：反射真实名后 DROP + CREATE（约定名）。
    """
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    current = None
    if bind.dialect.name == "sqlite":
        # 幂等：先反射当前 ondelete
        insp = sa.inspect(bind)
        for fk in insp.get_foreign_keys(table):
            if fk["constrained_columns"] == [col] and fk["referred_table"] == ref_table:
                current = fk["options"].get("ondelete")
        if current == "RESTRICT":
            return
        name = _conv_fk(table, col, ref_table)
        with op.batch_alter_table(table, naming_convention=_NC) as batch_op:
            batch_op.drop_constraint(name, type_="foreignkey")
            batch_op.create_foreign_key(
                name, ref_table, [col], ref_cols, ondelete="RESTRICT"
            )
        return
    # MySQL：约束迁移
    insp = sa.inspect(bind)
    for fk in insp.get_foreign_keys(table):
        if fk["constrained_columns"] != [col] or fk["referred_table"] != ref_table:
            continue
        if fk["options"].get("ondelete") == "RESTRICT":
            return
        old_name = fk["name"]
        if old_name:
            op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(
            _conv_fk(table, col, ref_table), table, ref_table, [col], ref_cols,
            ondelete="RESTRICT",
        )
        return


# ────────────────────────── generated_codes ──────────────────────────

def _migrate_generated_codes() -> None:
    # is_mock：对照 Model 为 Column(Integer, default=0)（nullable）——存量行 NULL=非 mock
    op.add_column("generated_codes", sa.Column("is_mock", sa.Integer(), nullable=True))
    op.add_column("generated_codes", sa.Column("source_steps_hash", sa.String(length=64), nullable=True))
    _set_fk_restrict("generated_codes", "case_id", "test_cases", ["id"])


# ────────────────────────── executions ──────────────────────────

def _migrate_executions() -> None:
    op.add_column("executions", sa.Column("manifest_json", sa.Text(), nullable=True))
    op.add_column("executions", sa.Column("runtime_state_json", sa.Text(), nullable=True))
    op.add_column("executions", sa.Column("stop_requested_at", sa.DateTime(), nullable=True))
    _set_fk_restrict("executions", "project_id", "projects", ["id"])


# ────────────────────────── execution_steps ──────────────────────────

def _migrate_execution_steps() -> None:
    op.add_column("execution_steps", sa.Column("assertion", sa.Text(), nullable=True))
    op.add_column("execution_steps", sa.Column("skip_reason", sa.String(length=50), nullable=True))
    op.add_column("execution_steps", sa.Column("error_type", sa.String(length=50), nullable=True))
    _set_fk_restrict("execution_steps", "execution_id", "executions", ["id"])
    _set_fk_restrict("execution_steps", "case_id", "test_cases", ["id"])
    with op.batch_alter_table("execution_steps", naming_convention=_NC) as batch_op:
        batch_op.create_unique_constraint(
            _conv_uq("execution_steps", "execution_id", "case_id", "step_index"),
            ["execution_id", "case_id", "step_index"],
        )


# ────────────────────────── execution_reports ──────────────────────────

def _migrate_execution_reports() -> None:
    bind = op.get_bind()
    old_unique_name = None
    if not context.is_offline_mode():
        insp = sa.inspect(bind)
        for uc in insp.get_unique_constraints("execution_reports"):
            if uc["column_names"] == ["execution_id"]:
                old_unique_name = uc["name"]
                break
    # SQLite：未命名 UNIQUE 反射为 None，batch 重建时由 naming_convention 重命名为
    # 约定名后再 drop（MySQL 则用反射出的真实约束名）。
    if old_unique_name is None and bind.dialect.name == "sqlite":
        old_unique_name = _conv_uq("execution_reports", "execution_id")
    # 顺序钉死：先建新复合 UNIQUE，再 drop 旧单列 UNIQUE —— MySQL 上 FK
    # (execution_id→executions) 依赖本表 execution_id 上的索引，若先 drop 单列
    # UNIQUE 会报 "Cannot drop index needed in a foreign key constraint"。
    with op.batch_alter_table("execution_reports", naming_convention=_NC) as batch_op:
        batch_op.add_column(sa.Column("report_type", sa.String(length=20), nullable=False, server_default="full"))
        batch_op.add_column(sa.Column("generation_status", sa.String(length=20), nullable=False, server_default="generating"))
        batch_op.add_column(sa.Column("claim_token", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_execution_reports_execution_type",
            ["execution_id", "report_type"],
        )
        if old_unique_name:
            batch_op.drop_constraint(old_unique_name, type_="unique")
    _set_fk_restrict("execution_reports", "execution_id", "executions", ["id"])


# ────────────────────────── heal_records ──────────────────────────

def _migrate_heal_records() -> None:
    op.add_column("heal_records", sa.Column("execution_id", sa.Integer(), nullable=True))
    op.add_column("heal_records", sa.Column("case_id", sa.Integer(), nullable=True))
    op.add_column("heal_records", sa.Column("round_no", sa.Integer(), server_default="1", nullable=True))
    op.add_column("heal_records", sa.Column("original_code_id", sa.Integer(), nullable=True))
    op.add_column("heal_records", sa.Column("healed_code_id", sa.Integer(), nullable=True))
    op.add_column("heal_records", sa.Column("error_type", sa.String(length=50), nullable=True))
    op.add_column("heal_records", sa.Column(
        "root_execution_step_id", sa.Integer(), nullable=True,
    ))
    # 新 FK（SQLite ADD COLUMN 允许 nullable 列的 REFERENCES；RESTRICT 语义由重建兜底）
    _set_fk_restrict("heal_records", "execution_step_id", "execution_steps", ["id"])
    with op.batch_alter_table("heal_records", naming_convention=_NC) as batch_op:
        batch_op.create_foreign_key(
            _conv_fk("heal_records", "execution_id", "executions"),
            "executions", ["execution_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            _conv_fk("heal_records", "case_id", "test_cases"),
            "test_cases", ["case_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            _conv_fk("heal_records", "original_code_id", "generated_codes"),
            "generated_codes", ["original_code_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            _conv_fk("heal_records", "healed_code_id", "generated_codes"),
            "generated_codes", ["healed_code_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            _conv_fk("heal_records", "root_execution_step_id", "execution_steps"),
            "execution_steps", ["root_execution_step_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_heal_records_exec_case_round",
            ["execution_id", "case_id", "round_no"],
        )


# ────────────────────────── 新建 batch 事实表 ──────────────────────────

def _create_batch_tables() -> None:
    op.create_table(
        "batch_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=True),
        sa.Column("is_valid_at_attempt", sa.Integer(), nullable=True),
        sa.Column("is_mock_at_attempt", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=50), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("kpi_eligible", sa.Integer(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["code_id"], ["generated_codes.id"], ondelete="RESTRICT"),
        # batch_id 不做 FK 到 batch_records（BatchCase 先生成、BatchRecord 收口时才创建）
        sa.UniqueConstraint("batch_id", "case_id", name="uq_batch_cases_batch_case"),
    )
    op.create_index("idx_batch_cases_batch_id", "batch_cases", ["batch_id"])

    op.create_table(
        "batch_records",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("batch_status", sa.String(length=20), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_TS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("batch_id", name="uq_batch_records_batch_id"),
    )


def upgrade() -> None:
    conn = op.get_bind()
    _preflight(conn)
    _migrate_generated_codes()
    _migrate_executions()
    _migrate_execution_steps()
    _migrate_execution_reports()
    _migrate_heal_records()
    _create_batch_tables()


def downgrade() -> None:
    """回滚：按依赖逆序删除新建表 / 列 / 约束。

    SQLite：不支持删除列，downgrade 只删新建表与复合 UNIQUE（列残留，但
    downgrade base 会 DROP 整表）。MySQL：完整 constraint migration（先 drop
    依赖复合 UNIQUE 的 FK，再 drop UNIQUE / 列，最后恢复 legacy FK CASCADE）。
    """
    bind = op.get_bind() if not context.is_offline_mode() else None
    is_sqlite = bind is not None and bind.dialect.name == "sqlite"

    for table in ("batch_records", "batch_cases"):
        if context.is_offline_mode() or sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)

    if is_sqlite:
        # SQLite 只支持重建表 drop 约束；列无法删除（0001 downgrade 会整表 DROP）
        with op.batch_alter_table("heal_records", naming_convention=_NC) as batch_op:
            batch_op.drop_constraint("uq_heal_records_exec_case_round", type_="unique")
        with op.batch_alter_table("execution_steps", naming_convention=_NC) as batch_op:
            batch_op.drop_constraint(_conv_uq("execution_steps", "execution_id", "case_id", "step_index"), type_="unique")
        return

    # ── MySQL：完整 constraint migration ──
    # 顺序钉死：先 drop 依赖复合 UNIQUE 的 FK，再 drop UNIQUE / 列，最后恢复 legacy FK

    # heal_records：drop 0002 新增 FK → 复合 UNIQUE → 新列 → 恢复 execution_step_id FK CASCADE
    for col, ref in [
        ("execution_id", "executions"), ("case_id", "test_cases"),
        ("original_code_id", "generated_codes"), ("healed_code_id", "generated_codes"),
        ("root_execution_step_id", "execution_steps"),
    ]:
        _drop_fk("heal_records", col, ref)
    with op.batch_alter_table("heal_records", naming_convention=_NC) as batch_op:
        batch_op.drop_constraint("uq_heal_records_exec_case_round", type_="unique")
        for col in ("execution_id", "case_id", "round_no", "original_code_id",
                    "healed_code_id", "error_type", "root_execution_step_id"):
            batch_op.drop_column(col)
    _set_fk_cascade("heal_records", "execution_step_id", "execution_steps", ["id"])

    # execution_steps：drop FK → 复合 UNIQUE → 新列 → 恢复 FK CASCADE
    _drop_fk("execution_steps", "execution_id", "executions")
    _drop_fk("execution_steps", "case_id", "test_cases")
    with op.batch_alter_table("execution_steps", naming_convention=_NC) as batch_op:
        batch_op.drop_constraint(_conv_uq("execution_steps", "execution_id", "case_id", "step_index"), type_="unique")
        for col in ("assertion", "skip_reason", "error_type"):
            batch_op.drop_column(col)
    _set_fk_cascade("execution_steps", "execution_id", "executions", ["id"])
    _set_fk_cascade("execution_steps", "case_id", "test_cases", ["id"])

    # execution_reports：drop FK → 复合 UNIQUE → 新列 → 恢复单列 UNIQUE + FK CASCADE
    _drop_fk("execution_reports", "execution_id", "executions")
    with op.batch_alter_table("execution_reports", naming_convention=_NC) as batch_op:
        batch_op.drop_constraint("uq_execution_reports_execution_type", type_="unique")
        for col in ("report_type", "generation_status", "claim_token", "claimed_at"):
            batch_op.drop_column(col)
        batch_op.create_unique_constraint(
            "uq_execution_reports_execution_id", ["execution_id"]
        )
    _set_fk_cascade("execution_reports", "execution_id", "executions", ["id"])

    # executions / generated_codes：恢复 FK CASCADE + drop 新列
    _set_fk_cascade("executions", "project_id", "projects", ["id"])
    with op.batch_alter_table("executions", naming_convention=_NC) as batch_op:
        for col in ("manifest_json", "runtime_state_json", "stop_requested_at"):
            batch_op.drop_column(col)
    _set_fk_cascade("generated_codes", "case_id", "test_cases", ["id"])
    with op.batch_alter_table("generated_codes", naming_convention=_NC) as batch_op:
        batch_op.drop_column("is_mock")
        batch_op.drop_column("source_steps_hash")


def _drop_fk(table: str, col: str, ref: str) -> None:
    """MySQL：反射真实 FK 名后 drop（旧库可能为 table_ibfk_N）。"""
    insp = sa.inspect(op.get_bind())
    for fk in insp.get_foreign_keys(table):
        if fk["constrained_columns"] == [col] and fk["referred_table"] == ref:
            op.drop_constraint(fk["name"], table, type_="foreignkey")
            return


def _set_fk_cascade(table: str, col: str, ref: str, ref_cols) -> None:
    """MySQL：把 (table, col)→ref 的 FK ondelete 改为 CASCADE（downgrade 恢复 legacy）。"""
    insp = sa.inspect(op.get_bind())
    for fk in insp.get_foreign_keys(table):
        if fk["constrained_columns"] != [col] or fk["referred_table"] != ref:
            continue
        if fk["options"].get("ondelete") in (None, "CASCADE"):
            return
        op.drop_constraint(fk["name"], table, type_="foreignkey")
        op.create_foreign_key(
            _conv_fk(table, col, ref), table, ref, [col], ref_cols, ondelete="CASCADE"
        )
        return
