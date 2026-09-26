"""P0-8 验收 4/5：Recovery 收敛（open Round → cancelled_by_recovery）+ Rerun Failure Precedence"""

import asyncio
import json
from datetime import datetime as dt

from app.services.heal_service import HealRoundService, RerunResult
from app.utils import terminal_reason as _tr
from app.utils.case_state_resolver import resolve, step_to_dict
from app.utils.step_canonicalizer import hash_steps

_VALID_WEB_CODE = "async def run_test(safe):\n    return {'success': True, 'steps': []}"


def _seed(db, project, case, code, status="running"):
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


class TestRecoveryConvergence:
    def test_recovery_cancels_open_rounds(self, db_session, sample_project, sample_test_case, sample_generated_code):
        """验收 4：崩溃恢复后 open Round（pending/retrying/finalizing）→ cancelled_by_recovery，Execution 正常 interrupted"""
        from app.models.heal_record import HealRecord
        from app.models.execution import Execution
        from app.services.execution_finalizer import ExecutionFinalizer

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        for idx, status in enumerate(("pending", "retrying", "finalizing")):
            db_session.add(HealRecord(
                execution_id=exec_obj.id,
                case_id=sample_test_case.id,
                round_no=idx + 1,  # UNIQUE(execution_id,case_id,round_no)：同 execution+case 一轮一条
                execution_step_id=step.id,
                root_execution_step_id=step.id,
                original_code_id=sample_generated_code.id,
                retry_status=status,
                attempts="[]",
            ))
        db_session.commit()

        ExecutionFinalizer(db_session).seal(exec_obj.id, "interrupted", _tr.INTERRUPTED)

        records = db_session.query(HealRecord).filter(
            HealRecord.execution_id == exec_obj.id,
        ).all()
        assert all(r.retry_status == "cancelled_by_recovery" for r in records)

        exec_row = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert exec_row.status == "interrupted"
        assert exec_row.end_time is not None

    def test_infra_rerun_failure_upgrades_execution_failed(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """Rerun Failure Precedence：rerun 因 worker_failed 终止 → Round 按基础设施故障收口、
        error_type=worker_failed、Case 升级 execution_failed"""
        from app.models.heal_record import HealRecord
        from app.models.execution_step import ExecutionStep

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)

        mocker.patch.object(
            HealRoundService, "_ai_attempt",
            new=mocker.AsyncMock(return_value=(True, _VALID_WEB_CODE, None)),
        )
        mocker.patch.object(
            HealRoundService, "_rerun_case",
            new=mocker.AsyncMock(return_value=RerunResult(
                ok=False, error_type="worker_failed", error_message="候选代码注入失败",
            )),
        )

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "failed"
        assert result.error_type == "worker_failed"
        assert result.upgrade_execution_failed is True

        # canonical failed step 被写入真实基础设施事实 → Resolver 归 execution_failed
        rec = db_session.query(HealRecord).first()
        assert rec.error_type == "worker_failed"
        step_row = db_session.query(ExecutionStep).filter(
            ExecutionStep.id == step.id,
        ).first()
        assert step_row.error_type == "worker_failed"
        status, reason = resolve([step_to_dict(step_row)], {"reason": _tr.EXECUTION_FAILED})
        assert status == "failed"
        assert reason == _tr.EXECUTION_FAILED

    def test_heal_exhausted_mixed_failure(
        self, db_session, sample_project, sample_test_case, sample_generated_code, mocker,
    ):
        """混合失败钉死：≥1 candidate 过 Validator 且被实际 rerun、但全部 rerun 失败（非基础设施）→ heal_exhausted"""
        from app.models.heal_record import HealRecord

        exec_obj, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)

        mocker.patch.object(
            HealRoundService, "_ai_attempt",
            new=mocker.AsyncMock(return_value=(True, _VALID_WEB_CODE, None)),
        )
        mocker.patch.object(
            HealRoundService, "_rerun_case",
            new=mocker.AsyncMock(return_value=RerunResult(
                ok=False, error_type="case_rerun_failed", error_message="业务断言失败",
            )),
        )

        result = asyncio.run(HealRoundService(db_session).heal_case(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            project_id=sample_project.id,
            page=_mock_page(mocker),
            platform="web",
        ))
        assert result.retry_status == "failed"
        assert result.error_type == "heal_exhausted"
        assert result.upgrade_execution_failed is False

        # 【Heal 失败不改写原 Case 语义】：business_failure 仍是 business_failure（不升级 execution_failed）
        from app.models.execution_step import ExecutionStep
        step_row = db_session.query(ExecutionStep).filter(ExecutionStep.id == step.id).first()
        assert step_row.error_type == "element_not_found"
