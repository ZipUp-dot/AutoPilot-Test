"""Execution 终态收敛（ExecutionFinalizer.seal）集成测试（P0-7）

覆盖验收：
  1. 四种收敛路径（completed/stopped/failed/interrupted）各一测试，
     收敛后半终态（Execution terminal + case pending/running）为零。
  2. 收敛顺序钉死：收敛前快照跑 Resolver，unknown → 立即记 integrity_anomaly，
     且 Case 的 terminal_reason 保持 integrity_anomaly（不被改写覆盖）。
  3. counters 与 Resolver 结果一致，Seal 后不再变化。
  4. 幂等：二次 Seal 返回同一快照。
  5. 开放 HealRound → cancelled_by_recovery。
  6. 多失败 Step 优先级在 Seal 全链路生效（business > context user_stopped）。
"""

from datetime import datetime

from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.heal_record import HealRecord
from app.services.execution_finalizer import ExecutionFinalizer
from app.utils import terminal_reason as _tr
from app.utils.case_state_resolver import resolve, step_to_dict


def _make_execution(db, project_id, total=1, status="running"):
    ex = Execution(
        project_id=project_id,
        total_cases=total,
        passed_cases=0,
        failed_cases=0,
        progress=0,
        status=status,
        start_time=datetime.utcnow(),
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


def _add_step(db, execution_id, case_id, index, status,
              error_type=None, skip_reason=None, exception_type=None):
    s = ExecutionStep(
        execution_id=execution_id,
        case_id=case_id,
        step_index=index,
        action="goto" if index == 1 else "click",
        status=status,
        error_type=error_type,
        skip_reason=skip_reason,
        exception_type=exception_type,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _steps_of(db, execution_id, case_id):
    return (
        db.query(ExecutionStep)
        .filter(ExecutionStep.execution_id == execution_id,
                ExecutionStep.case_id == case_id)
        .order_by(ExecutionStep.step_index)
        .all()
    )


def _assert_no_semi_terminal(db, execution_id):
    """半终态为零：Seal 后不允许 Execution terminal 而 Case 仍 pending/running"""
    leftover = (
        db.query(ExecutionStep)
        .filter(ExecutionStep.execution_id == execution_id,
                ExecutionStep.status.in_(["pending", "running"]))
        .count()
    )
    assert leftover == 0


class TestSealPaths:
    """四种收敛路径（completed/stopped/failed/interrupted）+ 半终态为零"""

    def test_seal_completed(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 101, 1, "success")
        _add_step(db_session, ex.id, 101, 2, "success")

        snap = ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        db_session.refresh(ex)
        assert ex.status == "completed"
        assert ex.end_time is not None
        assert ex.passed_cases == 1
        assert ex.failed_cases == 0
        assert ex.progress == 100
        assert snap["status"] == "completed"
        _assert_no_semi_terminal(db_session, ex.id)

    def test_seal_stopped_user_stopped(self, db_session, sample_project):
        """验收：success+skipped(user_stopped) → failed(incomplete) + user_stopped"""
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 102, 1, "success")
        _add_step(db_session, ex.id, 102, 2, "pending")

        ExecutionFinalizer(db_session).seal(ex.id, "stopped", _tr.USER_STOPPED)

        db_session.refresh(ex)
        assert ex.status == "stopped"
        assert ex.failed_cases == 1  # success+skipped → failed（incomplete）
        assert ex.passed_cases == 0
        steps = _steps_of(db_session, ex.id, 102)
        assert [s.status for s in steps] == ["success", "skipped"]
        assert steps[1].skip_reason == "user_stopped"
        status, reason = resolve([step_to_dict(s) for s in steps], {"reason": _tr.USER_STOPPED})
        assert (status, reason) == ("failed", _tr.USER_STOPPED)
        _assert_no_semi_terminal(db_session, ex.id)

    def test_seal_failed_incomplete(self, db_session, sample_project):
        """验收：success+skipped(execution_failed) → terminal_reason=execution_failed"""
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 103, 1, "success")
        _add_step(db_session, ex.id, 103, 2, "running")

        ExecutionFinalizer(db_session).seal(ex.id, "failed", _tr.EXECUTION_FAILED)

        db_session.refresh(ex)
        assert ex.status == "failed"
        assert ex.failed_cases == 1
        steps = _steps_of(db_session, ex.id, 103)
        assert steps[1].status == "failed"
        assert steps[1].error_type == "incomplete_execution"  # running→failed(中性)
        status, reason = resolve([step_to_dict(s) for s in steps], {"reason": _tr.EXECUTION_FAILED})
        assert status == "failed"
        assert reason == _tr.EXECUTION_FAILED
        _assert_no_semi_terminal(db_session, ex.id)

    def test_seal_interrupted_skipped(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 104, 1, "pending")
        _add_step(db_session, ex.id, 104, 2, "pending")

        ExecutionFinalizer(db_session).seal(ex.id, "interrupted", _tr.INTERRUPTED)

        db_session.refresh(ex)
        assert ex.status == "interrupted"
        steps = _steps_of(db_session, ex.id, 104)
        assert all(s.status == "skipped" for s in steps)
        assert all(s.skip_reason == "interrupted" for s in steps)
        status, reason = resolve([step_to_dict(s) for s in steps], {"reason": _tr.INTERRUPTED})
        assert (status, reason) == ("skipped", _tr.INTERRUPTED)
        assert ex.passed_cases == 0
        assert ex.failed_cases == 0
        _assert_no_semi_terminal(db_session, ex.id)


class TestIntegrityAnomaly:
    """收敛顺序①：收敛前快照 unknown → 立即记 integrity_anomaly，防改写覆盖"""

    def test_malformed_step_forces_integrity(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 105, 1, "success")
        _add_step(db_session, ex.id, 105, 2, "bogus")  # 畸形状态

        snap = ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        db_session.refresh(ex)
        # ③ 最终 Execution 固定 failed(integrity_anomaly)
        assert ex.status == "failed"
        assert snap["status"] == "failed"
        assert snap["integrity_anomaly"] is True
        steps = _steps_of(db_session, ex.id, 105)
        # ② 改写后：所有步骤带 integrity_anomaly 标记，bogus → failed
        assert all(s.error_type == "integrity_anomaly" for s in steps)
        assert steps[1].status == "failed"
        # 该 Case terminal_reason 保持 integrity_anomaly（不得被改写后 incomplete 覆盖）
        status, reason = resolve(
            [step_to_dict(s) for s in steps], {"reason": _tr.NORMAL_SUCCESS}
        )
        assert status == "failed"
        assert reason == _tr.INTEGRITY_ANOMALY
        _assert_no_semi_terminal(db_session, ex.id)

    def test_integrity_kept_across_stop(self, db_session, sample_project):
        """integrity 在 stopped 路径同样保持（不被 context user_stopped 覆盖）"""
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 106, 1, "weird")

        ExecutionFinalizer(db_session).seal(ex.id, "stopped", _tr.USER_STOPPED)

        db_session.refresh(ex)
        assert ex.status == "failed"
        steps = _steps_of(db_session, ex.id, 106)
        status, reason = resolve([step_to_dict(s) for s in steps], {"reason": _tr.USER_STOPPED})
        assert (status, reason) == ("failed", _tr.INTEGRITY_ANOMALY)


class TestCountersAndIdempotency:
    """counters 与 Resolver 结果一致，Seal 后不再变化；幂等"""

    def test_counters_match_resolver(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id, total=2)
        # case A: 全 success → passed；case B: 含 failed → failed
        _add_step(db_session, ex.id, 107, 1, "success")
        _add_step(db_session, ex.id, 107, 2, "success")
        _add_step(db_session, ex.id, 108, 1, "success")
        _add_step(db_session, ex.id, 108, 2, "failed", error_type="business_assertion_failed")

        ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        db_session.refresh(ex)
        assert ex.passed_cases == 1
        assert ex.failed_cases == 1
        assert ex.progress == 100
        # Seal 后 counters 不再变化（Derived Cache 一次性重算）
        ExecutionFinalizer(db_session).seal(ex.id, "failed", _tr.EXECUTION_FAILED)
        db_session.refresh(ex)
        assert ex.passed_cases == 1
        assert ex.failed_cases == 1
        assert ex.status == "completed"  # 幂等：已终态不重复改写

    def test_seal_idempotent_snapshot(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 109, 1, "success")

        snap1 = ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)
        snap2 = ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        assert snap1["status"] == snap2["status"] == "completed"
        assert snap1["passed_cases"] == snap2["passed_cases"] == 1

    def test_seal_unknown_execution(self, db_session, sample_project):
        snap = ExecutionFinalizer(db_session).seal(999999, "completed", _tr.NORMAL_SUCCESS)
        assert snap["status"] == "unknown"


class TestHealRoundCancel:
    """开放 HealRound → cancelled_by_recovery"""

    def test_open_heal_round_cancelled(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id)
        step = _add_step(db_session, ex.id, 110, 1, "failed", error_type="element_not_found")
        hr = HealRecord(execution_step_id=step.id, retry_status="retrying", retry_count=1)
        db_session.add(hr)
        db_session.commit()

        ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        db_session.refresh(hr)
        assert hr.retry_status == "cancelled_by_recovery"

    def test_terminal_heal_round_untouched(self, db_session, sample_project):
        ex = _make_execution(db_session, sample_project.id)
        step = _add_step(db_session, ex.id, 111, 1, "failed", error_type="element_not_found")
        hr = HealRecord(execution_step_id=step.id, retry_status="failed", retry_count=3)
        db_session.add(hr)
        db_session.commit()

        ExecutionFinalizer(db_session).seal(ex.id, "completed", _tr.NORMAL_SUCCESS)

        db_session.refresh(hr)
        assert hr.retry_status == "failed"


class TestEvidencePriority:
    """多失败 Step 优先级在 Seal 全链路生效；business > context user_stopped"""

    def test_business_beats_stop_context(self, db_session, sample_project):
        """Case 先真实业务失败，后来的 Stop 不得伪装成未执行"""
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 112, 1, "success")
        _add_step(db_session, ex.id, 112, 2, "failed", error_type="business_assertion_failed")
        _add_step(db_session, ex.id, 112, 3, "pending")

        ExecutionFinalizer(db_session).seal(ex.id, "stopped", _tr.USER_STOPPED)

        db_session.refresh(ex)
        assert ex.status == "stopped"
        steps = _steps_of(db_session, ex.id, 112)
        # 有真实失败证据的 Case：pending 步骤不写 skip_reason（中性，防伪造）
        assert steps[2].status == "skipped"
        assert steps[2].skip_reason is None
        status, reason = resolve([step_to_dict(s) for s in steps], {"reason": _tr.USER_STOPPED})
        assert (status, reason) == ("failed", _tr.BUSINESS_FAILURE)
        assert ex.failed_cases == 1

    def test_pending_skip_reason_pre_start_drift(self, db_session, sample_project):
        """drift 收敛：pending → skipped(pre_start_drift)，reason=execution_failed"""
        ex = _make_execution(db_session, sample_project.id)
        _add_step(db_session, ex.id, 113, 1, "pending")
        _add_step(db_session, ex.id, 113, 2, "pending")

        ExecutionFinalizer(db_session).seal(
            ex.id, "failed", _tr.EXECUTION_FAILED, pending_skip_reason="pre_start_drift"
        )

        db_session.refresh(ex)
        assert ex.status == "failed"
        steps = _steps_of(db_session, ex.id, 113)
        assert all(s.status == "skipped" for s in steps)
        assert all(s.skip_reason == "pre_start_drift" for s in steps)
        assert all(s.error_type == "pre_start_drift" for s in steps)
        status, reason = resolve([step_to_dict(s) for s in steps], {"reason": _tr.EXECUTION_FAILED})
        assert (status, reason) == ("skipped", _tr.EXECUTION_FAILED)
