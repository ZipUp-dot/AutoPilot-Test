"""Execution Seal 测试（P0-10 验收 1）— Seal 后写路径被拒 + Terminal Commit Atomicity

覆盖验收 1：
  ① Seal 后任何修改 Manifest/Runtime/Step/HealRecord 的尝试被拒（guard_not_sealed
     抛 SealedExecutionError(409)）；非终态执行放行。
  ② Terminal Commit Atomicity：status 终态 + Case/Step 收敛 + runtime_state 终值
     （case_status/terminal_reason）+ counters 冻结在同一 COMMIT 内完成（seal 返回后
     立即读库可见，非先 commit terminal 再异步 Seal）。
  ③ 终态 Execution 的自愈 claim 被应用层拒绝（HealRoundService.claim ok=False）。
"""

import json
from datetime import datetime

import pytest

from app.exceptions import SealedExecutionError
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.services.execution_finalizer import ExecutionFinalizer
from app.services.heal_service import HealRoundService
from app.utils import terminal_reason as _tr
from app.utils.case_state_resolver import resolve, step_to_dict
from app.utils.seal_guard import guard_not_sealed


def _make_execution(db, project_id, status="running", **kw):
    ex = Execution(
        project_id=project_id,
        total_cases=kw.pop("total_cases", 1),
        passed_cases=0,
        failed_cases=0,
        progress=0,
        status=status,
        start_time=datetime.utcnow(),
        **kw,
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


def _add_step(db, execution_id, case_id, index, status,
              error_type=None, skip_reason=None):
    s = ExecutionStep(
        execution_id=execution_id,
        case_id=case_id,
        step_index=index,
        action="goto" if index == 1 else "click",
        status=status,
        error_type=error_type,
        skip_reason=skip_reason,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _runtime(db, execution_id):
    row = db.query(Execution).filter(Execution.id == execution_id).first()
    return json.loads(row.runtime_state_json or "{}")


class TestSealGuard:
    """验收 ①：guard_not_sealed 拒绝终态 / 放行非终态"""

    @pytest.mark.parametrize("status", ["completed", "stopped", "failed", "interrupted"])
    def test_guard_rejects_all_terminal_statuses(self, db_session, sample_project, status):
        ex = _make_execution(db_session, sample_project.id, status=status)
        with pytest.raises(SealedExecutionError):
            guard_not_sealed(db_session, ex.id)

    def test_guard_allows_non_terminal(self, db_session, sample_project):
        for status in ("queued", "running", "healing"):
            _make_execution(db_session, sample_project.id, status=status)
        # 不抛异常即放行
        for ex in db_session.query(Execution).all():
            guard_not_sealed(db_session, ex.id)

    def test_guard_ignores_missing_execution(self, db_session):
        # Execution 不存在时 guard 不抛（由调用方按各自语义处理）
        guard_not_sealed(db_session, 99999)

    def test_heal_claim_rejected_on_sealed_execution(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
    ):
        """终态 Execution 的自愈 claim 被应用层拒绝（HealRecord 写路径守卫生效）"""
        ex = _make_execution(
            db_session, sample_project.id, status="interrupted",
            manifest_json=json.dumps({"project": {"name": "X", "target_url": "u"}}),
            runtime_state_json=json.dumps({str(sample_test_case.id): {"active_code_id": sample_generated_code.id}}),
        )
        step = _add_step(db_session, ex.id, sample_test_case.id, 1, "failed",
                         error_type="element_not_found")
        out = HealRoundService(db_session).claim(
            ex.id, sample_test_case.id, step.id, sample_generated_code.id
        )
        assert not out.ok
        assert "stop_requested" in out.reason or "sealed" in out.reason


class TestTerminalCommitAtomicity:
    """验收 ②：终态 COMMIT 与收敛 + runtime_state + counters 同一提交边界"""

    def test_seal_failed_writes_runtime_state_and_counters_in_one_commit(
        self, db_session, sample_project,
    ):
        ex = _make_execution(db_session, sample_project.id, status="running")
        _add_step(db_session, ex.id, 101, 1, "success")
        _add_step(db_session, ex.id, 101, 2, "failed",
                  error_type="business_assertion_failed")

        snap = ExecutionFinalizer(db_session).seal(ex.id, "failed", _tr.EXECUTION_FAILED)

        db_session.refresh(ex)
        # 终态 + counters（Derived Cache，Resolver 重算）
        assert ex.status == "failed"
        assert ex.end_time is not None
        assert ex.failed_cases == 1
        assert ex.passed_cases == 0
        assert snap["status"] == "failed"
        # runtime_state 终值（同一 COMMIT 内已写，Seal 后立即可见，非异步补写）
        rt = _runtime(db_session, ex.id)
        assert rt["101"]["case_status"] == "failed"
        assert rt["101"]["terminal_reason"] == _tr.BUSINESS_FAILURE
        # 口径验证：与 Resolver 一致（Report/Metrics 只读 sealed runtime_state，不重跑）
        steps = (
            db_session.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == ex.id)
            .order_by(ExecutionStep.step_index)
            .all()
        )
        status, reason = resolve(
            [step_to_dict(s) for s in steps], {"reason": _tr.EXECUTION_FAILED}
        )
        assert (status, reason) == ("failed", _tr.BUSINESS_FAILURE)

    def test_seal_completed_normal_success(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id, status="running")
        _add_step(db_session, ex.id, 201, 1, "success")
        _add_step(db_session, ex.id, 201, 2, "success")

        ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        db_session.refresh(ex)
        assert ex.status == "completed"
        assert ex.passed_cases == 1
        rt = _runtime(db_session, ex.id)
        assert rt["201"]["case_status"] == "success"
        assert rt["201"]["terminal_reason"] == _tr.NORMAL_SUCCESS

    def test_seal_preserves_existing_runtime_code_id(self, db_session, sample_project):
        """runtime_state 终值写入保留 active_code_id（Report 事实源：最终代码）"""
        ex = _make_execution(
            db_session, sample_project.id, status="running",
            runtime_state_json=json.dumps(
                {"301": {"active_code_id": 42}}
            ),
        )
        _add_step(db_session, ex.id, 301, 1, "success")

        ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        rt = _runtime(db_session, ex.id)
        assert rt["301"]["active_code_id"] == 42
        assert rt["301"]["case_status"] == "success"
        assert rt["301"]["terminal_reason"] == _tr.NORMAL_SUCCESS

    def test_seal_stopped_user_stopped(self, db_session, sample_project):
        """success + skipped(user_stopped) → case_status failed + user_stopped"""
        ex = _make_execution(db_session, sample_project.id, status="running")
        _add_step(db_session, ex.id, 401, 1, "success")
        _add_step(db_session, ex.id, 401, 2, "pending")

        ExecutionFinalizer(db_session).seal_stopped(ex.id)

        db_session.refresh(ex)
        assert ex.status == "stopped"
        rt = _runtime(db_session, ex.id)
        assert rt["401"]["case_status"] == "failed"
        assert rt["401"]["terminal_reason"] == _tr.USER_STOPPED
