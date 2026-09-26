"""P0-8 验收 1/7：HealRound claim 竞态守卫（per-execution 串行化 + UNIQUE + stop/重复拒绝）"""

import asyncio
import json
import threading
from datetime import datetime as dt

from app.services.heal_service import HealRoundService
from app.utils.step_canonicalizer import hash_steps

_VALID_WEB_CODE = "async def run_test(safe):\n    return {'success': True, 'steps': []}"


def _seed(db, project, case, code, status="running", steps_status="failed"):
    """在当前 session 内物化 Execution + 一个失败 step + runtime_state 冻结代码"""
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep

    steps = json.loads(case.steps)
    exec_obj = Execution(
        project_id=project.id,
        total_cases=1,
        status=status,
        start_time=dt.utcnow(),
        manifest_json=json.dumps({"schema_version": 1, "cases": [{
            "case_id": case.id, "case_name": case.case_name,
            "step_count": len(steps),
            "steps_hash": hash_steps(steps),
            "original_code_id": code.id,
        }]}),
        runtime_state_json=json.dumps({str(case.id): {"active_code_id": code.id}}),
    )
    db.add(exec_obj)
    db.flush()
    step = ExecutionStep(
        execution_id=exec_obj.id,
        case_id=case.id,
        step_index=1,
        action="click",
        status=steps_status,
        error_type="element_not_found",
    )
    db.add(step)
    db.commit()
    return exec_obj, step


class TestClaimRoundGuard:
    def test_concurrent_claim_only_one_succeeds(
        self, file_db,
    ):
        """验收 1：两请求并发 claim 同 case → 仅一成功（DB 约束生效）"""
        engine, factory = file_db
        db = factory()
        from app.models.project import Project
        from app.models.test_case import TestCase
        from app.models.generated_code import GeneratedCode

        project = Project(
            name="Guard Project", target_url="https://example.com",
            test_path="/", status="active",
        )
        db.add(project)
        db.flush()
        case = TestCase(
            project_id=project.id, case_name="Guard Case", case_no="G01",
            priority="P0",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "Open"},
            ]),
            status="imported",
        )
        db.add(case)
        db.flush()
        code = GeneratedCode(
            case_id=case.id, code_content=_VALID_WEB_CODE, code_language="python",
            is_valid=1, source_steps_hash=hash_steps(json.loads(case.steps)),
        )
        db.add(code)
        db.commit()
        exec_obj, step = _seed(db, project, case, code)

        results: list[bool] = []
        barrier = threading.Barrier(2)

        def worker():
            s = factory()
            try:
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass
                out = HealRoundService(s).claim(
                    exec_obj.id, case.id, step.id, code.id
                )
                results.append(out.ok)
            finally:
                s.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(results) == 2
        assert sum(results) == 1
        db.close()

    def test_duplicate_claim_rejected(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
    ):
        """验收 7：同 execution+case 已存在 HealRecord → 拒绝再次 Heal（Manual 亦同）"""
        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        svc = HealRoundService(db_session)

        out1 = svc.claim(exec_obj.id, sample_test_case.id, step.id, sample_generated_code.id)
        assert out1.ok

        out2 = svc.claim(exec_obj.id, sample_test_case.id, step.id, sample_generated_code.id)
        assert not out2.ok
        assert "已存在 HealRecord" in out2.reason

        # Manual 路径同样被拒（claim 是统一入口）
        from app.models.heal_record import HealRecord
        assert db_session.query(HealRecord).count() == 1

    def test_claim_rejected_when_execution_terminal(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
    ):
        """stop_requested（终态/end_time）→ claim 拒绝"""
        exec_obj, step = _seed(
            db_session, sample_project, sample_test_case, sample_generated_code,
            status="completed",
        )
        out = HealRoundService(db_session).claim(
            exec_obj.id, sample_test_case.id, step.id, sample_generated_code.id
        )
        assert not out.ok
        assert "stop_requested" in out.reason

    def test_no_failed_step_skips_heal(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mocker,
    ):
        """无 failed step → 不触发 Heal，保留原 CaseResult/terminal_reason"""
        from app.models.execution_step import ExecutionStep

        exec_obj, step = _seed(
            db_session, sample_project, sample_test_case, sample_generated_code,
            steps_status="pending",
        )
        # 只保留 pending step（无失败证据）
        step.status = "pending"
        step.error_type = None
        db_session.commit()

        page = mocker.MagicMock()
        page.content = mocker.AsyncMock(return_value="<html></html>")
        page.evaluate = mocker.AsyncMock(return_value=[])

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=page,
            platform="web",
        ))
        assert result.retry_status == "skipped"
        assert result.rounds_consumed is False

        from app.models.heal_record import HealRecord
        assert db_session.query(HealRecord).count() == 0
