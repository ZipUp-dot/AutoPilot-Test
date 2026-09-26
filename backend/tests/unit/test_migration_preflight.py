"""迁移预检测试 — 孤儿行 / UNIQUE 冲突扫描（P0-11）

覆盖 app/db/migration_preflight.run_preflight：
  1. 干净 legacy 库 → 无问题清单
  2. 孤儿行（executions.project_id 无父 Project）→ 检出并给出样例 id
  3. execution_steps 复合 UNIQUE 冲突 → 检出
  4. 列尚不存在（0002 加列前的 heal_records 新列）→ 跳过不报错
"""

import pytest
from sqlalchemy import create_engine, text


def _build_legacy_schema(conn) -> None:
    """按 0001 legacy 结构建最小表集（仅 preflight 涉及的边）"""
    conn.execute(text("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            target_url VARCHAR(500) NOT NULL
        )
    """))
    conn.execute(text("""
        CREATE TABLE test_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            case_name VARCHAR(255) NOT NULL,
            steps TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE generated_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            code_content TEXT NOT NULL,
            FOREIGN KEY (case_id) REFERENCES test_cases(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            status VARCHAR(20) DEFAULT 'queued',
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE execution_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id INTEGER NOT NULL,
            case_id INTEGER NOT NULL,
            step_index INTEGER NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id),
            FOREIGN KEY (case_id) REFERENCES test_cases(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE execution_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id INTEGER NOT NULL,
            report_html TEXT,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE heal_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_step_id INTEGER NOT NULL,
            original_code TEXT,
            FOREIGN KEY (execution_step_id) REFERENCES execution_steps(id)
        )
    """))
    conn.commit()


@pytest.fixture
def legacy_engine(tmp_path):
    """带完整 legacy 最小结构的文件版 SQLite engine"""
    url = f"sqlite:///{(tmp_path / 'preflight.db').as_posix()}"
    eng = create_engine(url)
    with eng.begin() as conn:
        _build_legacy_schema(conn)
    return eng


class TestPreflightClean:
    def test_clean_legacy_db_passes(self, legacy_engine):
        """干净 legacy 库 → 无问题"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        assert issues == [], f"干净库不应有预检问题: {issues}"


class TestPreflightOrphanRows:
    def test_orphan_execution_detected_with_samples(self, legacy_engine):
        """executions.project_id 引用不存在 Project → 检出孤儿行与样例 id"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.begin() as conn:
            conn.execute(text("INSERT INTO projects (id, name, target_url) VALUES (1, 'p', 'http://x')"))
            conn.execute(text("INSERT INTO executions (id, project_id) VALUES (1, 1)"))
            conn.execute(text("INSERT INTO executions (id, project_id) VALUES (2, 999)"))
            conn.execute(text("INSERT INTO executions (id, project_id) VALUES (3, 888)"))

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        assert any("executions.project_id → projects" in i and "存在 2 条无父引用" in i
                   and "样例 id=[2, 3]" in i for i in issues), issues

    def test_orphan_case_reference_detected(self, legacy_engine):
        """generated_codes.case_id 无父 TestCase → 检出"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.begin() as conn:
            conn.execute(text("INSERT INTO generated_codes (id, case_id, code_content) VALUES (1, 42, 'x')"))

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        assert any("generated_codes.case_id → test_cases" in i for i in issues), issues

    def test_null_fk_ignored(self, legacy_engine):
        """NULL FK 不是孤儿行（heal_records 新列全 NULL → 不检出）"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.begin() as conn:
            # heal_records 旧列 execution_step_id 无法为 NULL（NOT NULL），
            # 先建一条合法行，验证其不触发孤儿
            conn.execute(text("INSERT INTO projects (id, name, target_url) VALUES (1, 'p', 'http://x')"))
            conn.execute(text("INSERT INTO test_cases (id, project_id, case_name, steps) VALUES (1, 1, 'c', '[]')"))
            conn.execute(text("INSERT INTO executions (id, project_id) VALUES (1, 1)"))
            conn.execute(text("INSERT INTO execution_steps (id, execution_id, case_id, step_index) VALUES (1, 1, 1, 0)"))
            conn.execute(text("INSERT INTO heal_records (id, execution_step_id) VALUES (1, 1)"))

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        assert not any("heal_records" in i for i in issues), issues


class TestPreflightUniqueConflicts:
    def test_execution_steps_unique_conflict_detected(self, legacy_engine):
        """execution_steps(execution_id,case_id,step_index) 重复 → 检出"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.begin() as conn:
            conn.execute(text("INSERT INTO projects (id, name, target_url) VALUES (1, 'p', 'http://x')"))
            conn.execute(text("INSERT INTO test_cases (id, project_id, case_name, steps) VALUES (1, 1, 'c', '[]')"))
            conn.execute(text("INSERT INTO executions (id, project_id) VALUES (1, 1)"))
            conn.execute(text("INSERT INTO execution_steps (id, execution_id, case_id, step_index) VALUES (1, 1, 1, 0)"))
            conn.execute(text("INSERT INTO execution_steps (id, execution_id, case_id, step_index) VALUES (2, 1, 1, 0)"))

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        assert any("uq_execution_steps_execution_id_case_id_step_index" in i
                   and "(1, 1, 0)" in i for i in issues), issues

    def test_execution_reports_unique_conflict_detected(self, legacy_engine):
        """execution_reports(execution_id,report_type) 尚无法检查（列不存在）→ 跳过"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        # report_type 列在 legacy 库不存在，预检必须跳过而非报错
        assert not any("execution_reports" in i for i in issues), issues

    def test_unique_check_skips_missing_columns(self, legacy_engine):
        """heal_records 新列（execution_id/case_id/round_no）不存在 → 不报错跳过"""
        from app.db.migration_preflight import run_preflight

        with legacy_engine.connect() as conn:
            issues = run_preflight(conn)
        assert not any("uq_heal_records" in i for i in issues), issues
