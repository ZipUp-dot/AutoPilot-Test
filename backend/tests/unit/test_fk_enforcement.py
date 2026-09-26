"""FK 强制测试 — SQLite PRAGMA + ORM passive_deletes 证明（P0-11）

验收：
  4. SQLite PRAGMA foreign_keys=ON 生效：删除有 Execution 的 Project → IntegrityError
  6. ORM 层 session.delete(被引用 parent) → IntegrityError、子记录不变
     （passive_deletes 生效证明：ORM 不先删子记录，由 DB 层 FK RESTRICT 阻断）

使用真实文件版 SQLite（WAL + PRAGMA foreign_keys=ON），模型 create_all 建表。
"""

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def fk_engine(tmp_path):
    """文件版 SQLite 引擎，PRAGMA foreign_keys=ON（模拟 database.py 事件监听）"""
    import app.models  # noqa: F401 — 注册全部模型
    from app.db.database import Base

    url = f"sqlite:///{(tmp_path / 'fk.db').as_posix()}"
    eng = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture
def fk_session(fk_engine):
    Session = sessionmaker(bind=fk_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    fk_engine.dispose()


def _seed_chain(s, with_heal=False):
    """构造最小历史事实链：Project→TestCase→GeneratedCode / Execution→Step→Report(+Heal)"""
    from app.models.project import Project
    from app.models.test_case import TestCase
    from app.models.generated_code import GeneratedCode
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep
    from app.models.report import Report

    project = Project(name="P", target_url="https://a.com")
    s.add(project)
    s.flush()

    case = TestCase(project_id=project.id, case_name="C", steps="[]")
    s.add(case)
    s.flush()

    code = GeneratedCode(case_id=case.id, code_content="def x(): pass")
    s.add(code)
    s.flush()

    exec_obj = Execution(project_id=project.id, status="completed")
    s.add(exec_obj)
    s.flush()

    step = ExecutionStep(execution_id=exec_obj.id, case_id=case.id,
                         step_index=0, status="success")
    s.add(step)
    s.flush()

    report = Report(execution_id=exec_obj.id, report_type="full")
    s.add(report)
    s.flush()

    heal = None
    if with_heal:
        from app.models.heal_record import HealRecord
        heal = HealRecord(
            execution_step_id=step.id,
            execution_id=exec_obj.id,
            case_id=case.id,
            round_no=1,
            original_code_id=code.id,
            healed_code_id=None,  # 允许 NULL（Heal failed/cancelled）
        )
        s.add(heal)
        s.flush()

    s.commit()
    return project, case, code, exec_obj, step, report, heal


class TestPragmaEnforcement:
    """验收 4：SQLite PRAGMA foreign_keys=ON 生效"""

    def test_delete_project_with_execution_raises_integrity_error(self, fk_session, fk_engine):
        """删除有 Execution 的 Project → IntegrityError（RESTRICT 由 DB 层阻断）"""
        project, *_ = _seed_chain(fk_session)

        with pytest.raises(IntegrityError):
            fk_session.delete(project)
            fk_session.commit()
        fk_session.rollback()

        # 回滚后数据仍在
        count = fk_engine.connect().execute(
            text("SELECT COUNT(*) FROM projects")).scalar()
        assert count == 1, "删除被阻断后 Project 应保留"

    def test_pragma_is_on(self, fk_engine):
        """连接级 PRAGMA foreign_keys 确认 ON（证明 listener 生效）"""
        with fk_engine.connect() as conn:
            val = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert val == 1, f"PRAGMA foreign_keys 应为 ON，实际 {val}"


class TestOrmDeleteEnforcement:
    """验收 6：ORM session.delete(parent) → IntegrityError、子记录不变"""

    def test_delete_test_case_with_generated_code_blocked(self, fk_session, fk_engine):
        """删除有 GeneratedCode 的 TestCase → IntegrityError，代码记录不变"""
        _, case, code, *_ = _seed_chain(fk_session)
        code_id = code.id

        with pytest.raises(IntegrityError):
            fk_session.delete(case)
            fk_session.commit()
        fk_session.rollback()

        row = fk_engine.connect().execute(
            text("SELECT COUNT(*) FROM generated_codes WHERE id = :cid"),
            {"cid": code_id}).scalar()
        assert row == 1, "子记录（generated_codes）应保持不变"

    def test_delete_execution_with_steps_blocked(self, fk_session, fk_engine):
        """删除有 ExecutionStep 的 Execution → IntegrityError，步骤记录不变"""
        _, _, _, exec_obj, step, *_ = _seed_chain(fk_session)
        step_id = step.id

        with pytest.raises(IntegrityError):
            fk_session.delete(exec_obj)
            fk_session.commit()
        fk_session.rollback()

        row = fk_engine.connect().execute(
            text("SELECT COUNT(*) FROM execution_steps WHERE id = :sid"),
            {"sid": step_id}).scalar()
        assert row == 1, "子记录（execution_steps）应保持不变"

    def test_delete_execution_with_report_blocked(self, fk_session, fk_engine):
        """删除有 ExecutionReport 的 Execution → IntegrityError"""
        _, _, _, exec_obj, _, report, _ = _seed_chain(fk_session)
        report_id = report.id

        with pytest.raises(IntegrityError):
            fk_session.delete(exec_obj)
            fk_session.commit()
        fk_session.rollback()

        row = fk_engine.connect().execute(
            text("SELECT COUNT(*) FROM execution_reports WHERE id = :rid"),
            {"rid": report_id}).scalar()
        assert row == 1, "子记录（execution_reports）应保持不变"

    def test_delete_step_with_heal_record_blocked(self, fk_session, fk_engine):
        """删除有 HealRecord 的 ExecutionStep → IntegrityError（含 heal 链）"""
        *_ , step, _, heal = _seed_chain(fk_session, with_heal=True)
        heal_id = heal.id

        with pytest.raises(IntegrityError):
            fk_session.delete(step)
            fk_session.commit()
        fk_session.rollback()

        row = fk_engine.connect().execute(
            text("SELECT COUNT(*) FROM heal_records WHERE id = :hid"),
            {"hid": heal_id}).scalar()
        assert row == 1, "子记录（heal_records）应保持不变"

    def test_delete_generated_code_used_as_original_code_id_blocked(self, fk_session, fk_engine):
        """删除被 heal_records.original_code_id 引用的 GeneratedCode → IntegrityError"""
        _, _, code, *_ = _seed_chain(fk_session, with_heal=True)

        with pytest.raises(IntegrityError):
            fk_session.delete(code)
            fk_session.commit()
        fk_session.rollback()

    def test_orphan_delete_still_allowed(self, fk_session):
        """无历史事实子记录的项目仍可删除（既有业务闭环不受影响）"""
        from app.models.project import Project
        p = Project(name="Empty", target_url="https://b.com")
        fk_session.add(p)
        fk_session.commit()
        pid = p.id

        fk_session.delete(p)
        fk_session.commit()
        assert fk_session.get(Project, pid) is None


class TestBatchFacts:
    """batch_cases / batch_records FK RESTRICT（生命周期相反，batch_id 无 FK）"""

    def test_batch_record_project_restrict(self, fk_session, fk_engine):
        """删除被 BatchRecord 引用的 Project → IntegrityError"""
        from app.models.project import Project
        from app.models.batch_records import BatchRecord

        p = Project(name="B", target_url="https://b.com")
        fk_session.add(p)
        fk_session.flush()
        fk_session.add(BatchRecord(project_id=p.id, batch_id="b1",
                                   batch_status="completed", summary_json="{}"))
        fk_session.commit()

        with pytest.raises(IntegrityError):
            fk_session.delete(p)
            fk_session.commit()
        fk_session.rollback()

    def test_batch_case_case_restrict(self, fk_session, fk_engine):
        """删除被 batch_cases.case_id 引用的 TestCase → IntegrityError"""
        from app.models.test_case import TestCase
        from app.models.project import Project
        from app.models.batch_cases import BatchCase

        p = Project(name="B", target_url="https://b.com")
        fk_session.add(p)
        fk_session.flush()
        c = TestCase(project_id=p.id, case_name="C", steps="[]")
        fk_session.add(c)
        fk_session.flush()
        fk_session.add(BatchCase(project_id=p.id, batch_id="b1", case_id=c.id,
                                 status="success"))
        fk_session.commit()

        with pytest.raises(IntegrityError):
            fk_session.delete(c)
            fk_session.commit()
        fk_session.rollback()

    def test_batch_case_code_id_nullable(self, fk_session):
        """batch_cases.code_id 允许 NULL（slot_timeout/circuit_open 无代码场景）"""
        from app.models.test_case import TestCase
        from app.models.project import Project
        from app.models.batch_cases import BatchCase

        p = Project(name="B", target_url="https://b.com")
        fk_session.add(p)
        fk_session.flush()
        c = TestCase(project_id=p.id, case_name="C", steps="[]")
        fk_session.add(c)
        fk_session.flush()
        bc = BatchCase(project_id=p.id, batch_id="b1", case_id=c.id,
                       status="skipped", code_id=None)
        fk_session.add(bc)
        fk_session.commit()

        assert bc.code_id is None
