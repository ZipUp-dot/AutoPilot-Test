"""P0-8 验收 2：Candidate 隔离（失败 candidate 不落 generated_codes / 不可达有效代码）"""

import asyncio
import json
from datetime import datetime as dt

from app.services.heal_service import HealRoundService, RerunResult
from app.utils.step_canonicalizer import hash_steps

_VALID_WEB_CODE = "async def run_test(safe):\n    return {'success': True, 'steps': []}"
# 含 import os —— 必被 CodeValidator 拒绝（安全规则）
_INVALID_WEB_CODE = 'async def run_test(safe):\n    import os\n    os.system("echo hi")'


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


class TestCandidateIsolation:
    def test_failed_candidate_not_in_generated_codes(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """三个 attempt 全部未过 Validator → validation_error；失败 candidate 不落库"""
        from app.models.execution_step import ExecutionStep
        from app.models.generated_code import GeneratedCode

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        before = db_session.query(GeneratedCode).count()

        mocker.patch.object(
            HealRoundService, "_ai_attempt",
            new=mocker.AsyncMock(return_value=(True, _INVALID_WEB_CODE, None)),
        )
        rerun_spy = mocker.patch.object(
            HealRoundService, "_rerun_case",
            new=mocker.AsyncMock(return_value=RerunResult(ok=True, step_results={})),
        )

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "failed"
        assert result.error_type == "validation_error"
        # 未过 Validator → 不 rerun、不落库
        rerun_spy.assert_not_called()
        assert db_session.query(GeneratedCode).count() == before

        # 每个 attempt 必须记录 candidate_code/validator_result/rerun_result/rerun_error_type
        from app.models.heal_record import HealRecord
        rec = db_session.query(HealRecord).first()
        attempts = json.loads(rec.attempts)
        assert len(attempts) == 3
        for a in attempts:
            assert a["candidate_code"] == _INVALID_WEB_CODE
            assert a["validator_result"] is False
            assert a["rerun_result"] is None
            assert a["rerun_error_type"] is None

        # Current Effective Code 仍是原始冻结代码（失败 candidate 不可达）
        from app.services.execution_code_resolver import ExecutionCodeResolver
        active = ExecutionCodeResolver(db_session).get_active_code(exec_obj.id, sample_test_case.id)
        assert active.id == sample_generated_code.id
        assert active.code_content == sample_generated_code.code_content

    def test_winning_candidate_persisted_as_healed(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """rerun 成功 → 创建 GeneratedCode(is_healed=1, source_steps_hash=per-case)；Step=success"""
        from app.models.generated_code import GeneratedCode
        from app.models.heal_record import HealRecord

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)

        mocker.patch.object(
            HealRoundService, "_ai_attempt",
            new=mocker.AsyncMock(return_value=(True, _VALID_WEB_CODE, None)),
        )
        mocker.patch.object(
            HealRoundService, "_rerun_case",
            new=mocker.AsyncMock(return_value=RerunResult(
                ok=True, error_type=None,
                step_results={1: {"status": "success", "error_type": None, "skip_reason": None, "exception_type": None, "duration_ms": 10}},
            )),
        )
        mocker.patch.object(HealRoundService, "_manifest_steps_hash", return_value="per-case-hash-123")

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "success"
        assert result.error_type is None

        rec = db_session.query(HealRecord).first()
        assert rec.retry_status == "success"
        assert rec.error_type is None
        assert rec.healed_code_id == result.healed_code_id

        # winning candidate 落库：is_healed=1 + per-case steps_hash
        gen = db_session.query(GeneratedCode).filter(GeneratedCode.id == result.healed_code_id).first()
        assert gen is not None
        assert gen.is_healed == 1
        assert gen.is_valid == 1
        assert gen.source_steps_hash == "per-case-hash-123"
        assert gen.code_content == _VALID_WEB_CODE

        # runtime_state 唯一真源已切换 + canonical Step → success
        from app.models.execution import Execution
        exec_row = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        rs = json.loads(exec_row.runtime_state_json)
        assert rs[str(sample_test_case.id)]["active_code_id"] == gen.id
        assert rs[str(sample_test_case.id)]["case_status"] == "success"
        assert rs[str(sample_test_case.id)]["terminal_reason"] == "normal_success"

        from app.models.execution_step import ExecutionStep
        step_rows = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == sample_test_case.id,
        ).all()
        for s in step_rows:
            assert s.status == "success"

        # attempts 记录 rerun_result=success（Finalization Retry 凭此定位，绝不重调 AI）
        attempts = json.loads(rec.attempts)
        assert attempts[0]["validator_result"] is True
        assert attempts[0]["rerun_result"] == "success"
