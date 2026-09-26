"""P0-8 验收 3/5：Finalization 两阶段（Phase A 事实落盘 / Phase B 线性化 + Failure Classification）"""

import asyncio
import json
from datetime import datetime as dt

from app.services.heal_service import HealRoundService, RerunResult
from app.utils.step_canonicalizer import hash_steps

_VALID_WEB_CODE = "async def run_test(safe):\n    return {'success': True, 'steps': []}"


def _seed(db, project, case, code):
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep

    steps = json.loads(case.steps)
    exec_obj = Execution(
        project_id=project.id,
        total_cases=1,
        status="running",
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
        status="failed",
        error_type="element_not_found",
    )
    db.add(step)
    db.commit()
    return exec_obj, step


def _mock_page(mocker):
    page = mocker.MagicMock()
    page.content = mocker.AsyncMock(return_value="<html></html>")
    page.evaluate = mocker.AsyncMock(return_value=[])
    return page


def _base_round_mocks(mocker, rerun_side_effect=None):
    ai_mock = mocker.patch.object(
        HealRoundService, "_ai_attempt",
        new=mocker.AsyncMock(return_value=(True, _VALID_WEB_CODE, None)),
    )
    rerun_mock = mocker.AsyncMock(return_value=RerunResult(
        ok=True, error_type=None,
        step_results={1: {"status": "success", "error_type": None, "skip_reason": None, "exception_type": None, "duration_ms": 10}},
    ))
    if rerun_side_effect is not None:
        rerun_mock.side_effect = rerun_side_effect
    mocker.patch.object(HealRoundService, "_rerun_case", new=rerun_mock)
    return ai_mock, rerun_mock


class TestFinalization:
    def test_phase_b_retry_without_recalling_ai(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """验收 3（可重试部分）：Phase B 瞬时失败 → 同 Round 重试且不重调 AI；全部重试仍失败 → heal_finalization_error"""
        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        ai_mock, _ = _base_round_mocks(mocker)
        mocker.patch.object(
            HealRoundService, "_phase_b_finalize",
            new=mocker.MagicMock(return_value="retryable: database is locked"),
        )

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "failed"
        assert result.error_type == "heal_finalization_error"
        # 多次 Phase B 重试期间绝不重调 AI、不重 rerun
        assert ai_mock.call_count == 1

        from app.models.heal_record import HealRecord
        rec = db_session.query(HealRecord).first()
        assert rec.retry_status == "failed"
        assert rec.error_type == "heal_finalization_error"

    def test_retry_finalization_succeeds_and_commits(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
    ):
        """验收 3（重试成功部分）：finalizing → 重试 Phase B 成功 → active_code 切换、Step=success、
        GeneratedCode + HealRecord.healed_code_id 全部落库（缺一不可）"""
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.models.heal_record import HealRecord
        from app.models.generated_code import GeneratedCode

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        # 模拟 Phase A 已完成：finalizing 状态 + attempts 中已持久化 winning（rerun_result=success）
        rec = HealRecord(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            round_no=1,
            execution_step_id=step.id,
            root_execution_step_id=step.id,
            original_code_id=sample_generated_code.id,
            retry_status="finalizing",
            healed_code=_VALID_WEB_CODE,
            heal_prompt="fix it",
            attempts=json.dumps([{
                "attempt": 1,
                "candidate_code": _VALID_WEB_CODE,
                "validator_result": True,
                "rerun_result": "success",
                "rerun_error_type": None,
            }]),
        )
        db_session.add(rec)
        db_session.commit()

        result = HealRoundService(db_session).retry_finalization(exec_obj.id, sample_test_case.id)
        assert result.retry_status == "success"
        assert result.error_type is None

        db_session.refresh(rec)
        assert rec.retry_status == "success"
        assert rec.error_type is None
        assert rec.healed_code_id is not None

        gen = db_session.query(GeneratedCode).filter(GeneratedCode.id == rec.healed_code_id).first()
        assert gen is not None
        assert gen.is_healed == 1
        assert gen.code_content == _VALID_WEB_CODE

        exec_row = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        rs = json.loads(exec_row.runtime_state_json)
        assert rs[str(sample_test_case.id)]["active_code_id"] == gen.id
        assert rs[str(sample_test_case.id)]["case_status"] == "success"
        assert rs[str(sample_test_case.id)]["terminal_reason"] == "normal_success"

        for s in db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == sample_test_case.id,
        ).all():
            assert s.status == "success"

    def test_phase_b_stop_cancels_finalization(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """验收 5：rerun success 后、Phase B 前到达 Stop → Phase B 放弃 success Finalization、
        不切换 active_code_id、Round 收口 cancelled_by_recovery"""
        from app.models.generated_code import GeneratedCode
        from app.models.execution import Execution
        from app.services import execution_state

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        gen_before = db_session.query(GeneratedCode).count()
        active_before = sample_generated_code.id

        def _rerun_then_stop(*args, **kwargs):
            # rerun 成功后、Phase B 之前到达 Stop（模拟竞态）
            execution_state.set_stop_flag(exec_obj.id)
            return RerunResult(
                ok=True, error_type=None,
                step_results={1: {"status": "success", "error_type": None, "skip_reason": None, "exception_type": None, "duration_ms": 10}},
            )

        _base_round_mocks(mocker, rerun_side_effect=_rerun_then_stop)

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "cancelled_by_recovery"
        assert result.error_type is None

        # 不切换 active_code_id、不新增 GeneratedCode、canonical Step 保持失败原样
        assert db_session.query(GeneratedCode).count() == gen_before
        from app.models.heal_record import HealRecord
        rec = db_session.query(HealRecord).first()
        assert rec.retry_status == "cancelled_by_recovery"
        assert rec.healed_code_id is None
        exec_row = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        rs = json.loads(exec_row.runtime_state_json)
        assert rs[str(sample_test_case.id)]["active_code_id"] == active_before

        from app.models.execution_step import ExecutionStep
        step_row = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == sample_test_case.id,
        ).first()
        assert step_row.status == "failed"

    def test_phase_a_failure_closes_heal_finalization_error(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """Phase A 落盘失败 → Round 收口 failed(heal_finalization_error)"""
        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        _base_round_mocks(mocker)
        mocker.patch.object(HealRoundService, "_phase_a_persist", return_value=False)

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "failed"
        assert result.error_type == "heal_finalization_error"
