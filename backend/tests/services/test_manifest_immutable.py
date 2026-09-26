"""P0-6 验收 2: Manifest immutable + Pre-start Drift Guard

篡改 TestCase.steps → 启动校验拒绝执行（_guard_pre_start_drift）：
  - Execution 置 failed
  - 未启动 ExecutionStep 置 skipped(skip_reason/error_type=pre_start_drift)
  - Manifest 不变（Manifest 由物化时冻结，运行期 immutable）
"""

import json

import pytest
from sqlalchemy.orm import Session

from app.utils.step_canonicalizer import hash_steps

VALID_CODE = (
    'async def run_test(page):\n'
    '    return {"success": True, "steps": []}\n'
)


def _steps(*items):
    return [
        {"step_number": i + 1, "action": a, "target": t, "value": "", "description": ""}
        for i, (a, t) in enumerate(items)
    ]


def _make_case(db_session, project, steps=None):
    from app.models.test_case import TestCase
    case = TestCase(
        project_id=project.id, case_name="C", case_no="TC", priority="P1",
        status="imported",
        steps=json.dumps(steps or _steps(("navigate", "https://example.com"))),
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def _make_code(db_session, case_id, steps):
    from app.models.generated_code import GeneratedCode
    code = GeneratedCode(
        case_id=case_id, code_content=VALID_CODE, code_language="python",
        is_valid=1, is_mock=0, source_steps_hash=hash_steps(steps),
    )
    db_session.add(code)
    db_session.commit()
    return code


def _admit_materialize(db_session, project, case):
    from app.services.execution_admission_service import ExecutionAdmissionService
    svc = ExecutionAdmissionService(db_session)
    result = svc.admit(project.id, [case.id])
    assert result.ok
    exec_id = svc.materialize(result, batch_name="B", mode="headless")
    return exec_id, result.manifest


def _guard(db_session, exec_id, manifest):
    """调 orchestrator._guard_pre_start_drift，SessionLocal 换绑当前连接。"""
    from app.services.orchestrator import TestOrchestrator
    conn = db_session.get_bind()

    def make_session():
        return Session(bind=conn)

    import app.db.database as db_mod
    from unittest.mock import patch
    with patch.object(db_mod, "SessionLocal", make_session):
        return TestOrchestrator()._guard_pre_start_drift(exec_id, manifest)


@pytest.fixture
def project(db_session):
    from app.models.project import Project
    p = Project(name="Mm", target_url="https://example.com", status="active")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


class TestManifestImmutable:
    def test_admission_freeze_manifest_and_runtime(self, db_session, project):
        """物化后 Manifest.cases[] 与 runtime_state[] 均已冻结（per-case 快照）。"""
        case = _make_case(db_session, project)
        code = _make_code(db_session, case.id, json.loads(case.steps))
        exec_id, manifest = _admit_materialize(db_session, project, case)

        from app.models.execution import Execution
        ex = db_session.query(Execution).filter(Execution.id == exec_id).first()
        stored_manifest = json.loads(ex.manifest_json)
        stored_runtime = json.loads(ex.runtime_state_json)

        # per-case 元素显式含必需字段
        m = stored_manifest["cases"][0]
        assert m["case_id"] == case.id
        assert m["case_name"] == "C"
        assert m["priority"] == "P1"
        assert m["step_count"] == 1
        assert m["steps_hash"] == hash_steps(json.loads(case.steps))
        assert m["original_code_id"] == code.id
        # runtime_state active_code_id == manifest original_code_id（同一冻结代码）
        assert stored_runtime[str(case.id)]["active_code_id"] == m["original_code_id"]

    def test_drift_after_modification_rejects_start(self, db_session, project):
        """篡改 TestCase.steps → Guard 拒绝启动：Execution=failed，step=skipped。"""
        case = _make_case(db_session, project)
        _make_code(db_session, case.id, json.loads(case.steps))
        exec_id, manifest = _admit_materialize(db_session, project, case)

        # 篡改 TestCase.steps（漂移）
        case.steps = json.dumps(_steps(("click", "#btn")))
        db_session.commit()

        drifted = _guard(db_session, exec_id, manifest)
        assert drifted == [case.id]

        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        db_session.expire_all()
        ex = db_session.query(Execution).filter(Execution.id == exec_id).first()
        assert ex.status == "failed"
        steps = db_session.query(ExecutionStep).filter(ExecutionStep.execution_id == exec_id).all()
        assert steps
        for s in steps:
            assert s.status == "skipped"
            assert s.skip_reason == "pre_start_drift"
            assert s.error_type == "pre_start_drift"

        # Manifest 不变（Manifest immutable）
        assert json.loads(ex.manifest_json)["cases"][0]["steps_hash"] == manifest[0]["steps_hash"]

    def test_no_drift_allows_start(self, db_session, project):
        """steps 未改动 → Guard 不拦截，Execution 保持 queued。"""
        case = _make_case(db_session, project)
        _make_code(db_session, case.id, json.loads(case.steps))
        exec_id, manifest = _admit_materialize(db_session, project, case)

        drifted = _guard(db_session, exec_id, manifest)
        assert drifted == []

        from app.models.execution import Execution
        db_session.expire_all()
        ex = db_session.query(Execution).filter(Execution.id == exec_id).first()
        assert ex.status == "queued"