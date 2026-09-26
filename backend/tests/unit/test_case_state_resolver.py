"""CaseStateResolver 单元测试（P0-7）

覆盖：
  1. 正式 Resolver Truth Table 全部组合类别（全 pending / success+pending /
     success+skipped / 含 failed / 全 skipped / 畸形 / 空）。
  2. status × terminal_reason Pair Invariant（钉死组合，禁止扩张）。
  3. skip_reason → terminal_reason 钉死映射。
  4. terminalization context 映射（completed/stopped/failed/interrupted）。
  5. 多失败 Step 终态优先级：integrity_anomaly > execution_failed > business_failure >
     user_stopped > interrupted；business 断言失败优先于 context user_stopped。
  6. failed step 的 exception 为 Timeout/worker/deadline/circuit 类 → execution_failed。
  7. pending/running → terminal_reason NULL。
"""

import pytest

from app.utils import terminal_reason as _tr
from app.utils.case_state_resolver import resolve

# Pair Invariant（铁律 12，钉死）：合法 status×reason 组合
_VALID_PAIRS = {
    "success": {_tr.NORMAL_SUCCESS},
    "failed": {
        _tr.BUSINESS_FAILURE, _tr.USER_STOPPED, _tr.INTERRUPTED,
        _tr.EXECUTION_FAILED, _tr.INTEGRITY_ANOMALY,
    },
    "skipped": {_tr.USER_STOPPED, _tr.INTERRUPTED, _tr.EXECUTION_FAILED},
    "pending": {None},
    "running": {None},
    "unknown": {_tr.INTEGRITY_ANOMALY},
}


def _step(status, error_type=None, skip_reason=None, exception_type=None):
    return {
        "status": status,
        "error_type": error_type,
        "skip_reason": skip_reason,
        "exception_type": exception_type,
    }


def _assert_pair(status, reason):
    assert reason in _VALID_PAIRS[status], (
        f"非法组合 status={status} reason={reason}（Pair Invariant 违反）"
    )


# ═══════════════════════════════════════════════
# 1. Truth Table 全组合类别
# ═══════════════════════════════════════════════

class TestTruthTable:
    def test_all_pending(self):
        status, reason = resolve([_step("pending"), _step("pending")])
        assert status == "pending"
        _assert_pair(status, reason)

    def test_success_plus_pending_running(self):
        status, reason = resolve([_step("success"), _step("pending")])
        assert status == "running"
        _assert_pair(status, reason)

    def test_success_plus_running_running(self):
        status, reason = resolve([_step("success"), _step("running")])
        assert status == "running"
        _assert_pair(status, reason)

    def test_success_plus_skipped_failed(self):
        status, reason = resolve([_step("success"), _step("skipped")])
        assert status == "failed"  # 部分完成即被跳过 = 未完整执行
        _assert_pair(status, reason)

    def test_has_failed(self):
        status, reason = resolve([_step("success"), _step("failed", error_type="business_assertion_failed")])
        assert status == "failed"
        _assert_pair(status, reason)

    def test_all_skipped(self):
        status, reason = resolve([_step("skipped"), _step("skipped")])
        assert status == "skipped"
        _assert_pair(status, reason)

    def test_malformed_unknown(self):
        status, reason = resolve([_step("bogus")])
        assert status == "unknown"
        _assert_pair(status, reason)

    def test_empty_steps_pending(self):
        status, reason = resolve([])
        assert status == "pending"
        _assert_pair(status, reason)


# ═══════════════════════════════════════════════
# 2. Pair Invariant 全组合 × context 不泄漏非法 reason
# ═══════════════════════════════════════════════

class TestPairInvariant:
    _CONTEXTS = [None, "normal_success", "business_failure", "user_stopped",
                 "interrupted", "execution_failed", "integrity_anomaly"]

    @pytest.mark.parametrize("status", ["success", "failed", "skipped"])
    def test_bare_status_no_evidence(self, status):
        """无 error_type/skip_reason/exception 的步骤，任意 context 不得泄漏非法 reason"""
        for ctx in self._CONTEXTS:
            st, reason = resolve([_step(status)], {"reason": ctx})
            _assert_pair(st, reason)

    def test_failed_no_evidence_with_normal_success_context(self):
        """failed(无证据) + completed context → 不得返回 normal_success（非法）"""
        status, reason = resolve([_step("failed")], {"reason": _tr.NORMAL_SUCCESS})
        assert status == "failed"
        assert reason != _tr.NORMAL_SUCCESS
        _assert_pair(status, reason)

    def test_skipped_with_normal_success_context(self):
        status, reason = resolve([_step("skipped")], {"reason": _tr.NORMAL_SUCCESS})
        assert status == "skipped"
        assert reason not in (_tr.NORMAL_SUCCESS, _tr.BUSINESS_FAILURE)
        _assert_pair(status, reason)

    def test_success_context_never_leaks_to_failed(self):
        for ctx in self._CONTEXTS:
            status, reason = resolve([_step("success"), _step("skipped")], {"reason": ctx})
            assert status == "failed"
            assert reason != _tr.NORMAL_SUCCESS
            _assert_pair(status, reason)

    def test_pending_running_reason_null(self):
        for steps in ([_step("pending")], [_step("running")],
                      [_step("success"), _step("pending")]):
            status, reason = resolve(steps, {"reason": _tr.USER_STOPPED})
            assert status in ("pending", "running")
            assert reason is None


# ═══════════════════════════════════════════════
# 3. skip_reason → terminal_reason 钉死映射
# ═══════════════════════════════════════════════

class TestSkipReasonMapping:
    def test_user_stopped(self):
        status, reason = resolve([_step("skipped", skip_reason="user_stopped")])
        assert (status, reason) == ("skipped", _tr.USER_STOPPED)

    def test_interrupted(self):
        status, reason = resolve([_step("skipped", skip_reason="interrupted")])
        assert (status, reason) == ("skipped", _tr.INTERRUPTED)

    @pytest.mark.parametrize("skip_reason", [
        "circuit_open", "deadline_exceeded", "worker_failed", "pre_start_drift",
    ])
    def test_execution_failed_skips(self, skip_reason):
        status, reason = resolve([_step("skipped", skip_reason=skip_reason)])
        assert (status, reason) == ("skipped", _tr.EXECUTION_FAILED)

    def test_success_plus_skipped_user_stopped(self):
        """验收：success+skipped(user_stopped) → failed + user_stopped"""
        status, reason = resolve([_step("success"), _step("skipped", skip_reason="user_stopped")])
        assert (status, reason) == ("failed", _tr.USER_STOPPED)

    def test_success_plus_skipped_execution_failed(self):
        """验收：success+skipped(execution_failed) → failed + execution_failed"""
        status, reason = resolve([_step("success"), _step("skipped", skip_reason="execution_failed")])
        assert (status, reason) == ("failed", _tr.EXECUTION_FAILED)


# ═══════════════════════════════════════════════
# 4. terminalization context 映射（无具体失败证据时兜底）
# ═══════════════════════════════════════════════

class TestContextMapping:
    def test_completed_normal_success(self):
        status, reason = resolve([_step("success")], {"reason": _tr.NORMAL_SUCCESS})
        assert (status, reason) == ("success", _tr.NORMAL_SUCCESS)

    def test_stopped_user_stopped(self):
        status, reason = resolve([_step("skipped")], {"reason": _tr.USER_STOPPED})
        assert (status, reason) == ("skipped", _tr.USER_STOPPED)

    def test_interrupted_context(self):
        status, reason = resolve([_step("skipped")], {"reason": _tr.INTERRUPTED})
        assert (status, reason) == ("skipped", _tr.INTERRUPTED)

    def test_failed_context_execution_failed(self):
        status, reason = resolve([_step("skipped")], {"reason": _tr.EXECUTION_FAILED})
        assert (status, reason) == ("skipped", _tr.EXECUTION_FAILED)

    def test_unknown_context(self):
        status, reason = resolve([_step("skipped")], {"reason": "unknown"})
        assert status == "skipped"
        _assert_pair(status, reason)


# ═══════════════════════════════════════════════
# 5. 多失败 Step 终态优先级（钉死）
# ═══════════════════════════════════════════════

class TestPriority:
    def test_integrity_beats_execution(self):
        steps = [
            _step("failed", error_type="integrity_anomaly"),
            _step("failed", error_type="execution_failed"),
        ]
        status, reason = resolve(steps)
        assert (status, reason) == ("failed", _tr.INTEGRITY_ANOMALY)

    def test_execution_beats_business(self):
        """平台/基础设施失败优先于业务断言失败（归因保守原则）"""
        steps = [
            _step("failed", error_type="element_wait_timeout"),
            _step("failed", error_type="business_assertion_failed"),
        ]
        status, reason = resolve(steps)
        assert (status, reason) == ("failed", _tr.EXECUTION_FAILED)

    def test_business_beats_user_stopped(self):
        steps = [
            _step("failed", error_type="business_assertion_failed"),
            _step("skipped", skip_reason="user_stopped"),
        ]
        status, reason = resolve(steps)
        assert (status, reason) == ("failed", _tr.BUSINESS_FAILURE)

    def test_business_beats_context_user_stopped(self):
        """【优先级规则】真实业务断言失败优先于 context 的 user_stopped——
        Case 先真实地失败了，后来的 Stop 不得伪装成未执行"""
        steps = [_step("failed", error_type="business_assertion_failed")]
        status, reason = resolve(steps, {"reason": _tr.USER_STOPPED})
        assert (status, reason) == ("failed", _tr.BUSINESS_FAILURE)

    def test_user_stopped_beats_interrupted(self):
        steps = [
            _step("skipped", skip_reason="user_stopped"),
            _step("skipped", skip_reason="interrupted"),
        ]
        status, reason = resolve(steps)
        assert (status, reason) == ("skipped", _tr.USER_STOPPED)

    def test_business_alone(self):
        status, reason = resolve([_step("failed", error_type="business_assertion_failed")])
        assert (status, reason) == ("failed", _tr.BUSINESS_FAILURE)


# ═══════════════════════════════════════════════
# 6. exception 提示归因（failed 无 error_type → execution）
# ═══════════════════════════════════════════════

class TestExceptionHints:
    @pytest.mark.parametrize("exc", ["TimeoutError", "asyncio.TimeoutError",
                                     "WorkerFailed", "DeadlineExceeded",
                                     "CircuitOpenError"])
    def test_timeout_worker_deadline_circuit_execution(self, exc):
        steps = [_step("failed", exception_type=exc)]
        status, reason = resolve(steps)
        assert (status, reason) == ("failed", _tr.EXECUTION_FAILED)

    def test_bare_failed_defaults_execution(self):
        """failed 且无任何 error_type/exception → 保守归因 execution_failed"""
        status, reason = resolve([_step("failed")])
        assert (status, reason) == ("failed", _tr.EXECUTION_FAILED)

    def test_business_assertion_with_assertion_error(self):
        steps = [_step("failed", error_type="business_assertion_failed",
                       exception_type="AssertionError")]
        status, reason = resolve(steps)
        assert (status, reason) == ("failed", _tr.BUSINESS_FAILURE)

    def test_incomplete_execution_is_neutral(self):
        """incomplete_execution 只推 failed 状态，不参与 reason 推导"""
        status, reason = resolve(
            [_step("failed", error_type="incomplete_execution")],
            {"reason": _tr.USER_STOPPED},
        )
        assert status == "failed"
        # 中性证据 + failed → context 兜底（user_stopped 合法）
        assert reason == _tr.USER_STOPPED
        _assert_pair(status, reason)

    def test_incomplete_execution_alone_falls_back_execution(self):
        status, reason = resolve([_step("failed", error_type="incomplete_execution")])
        assert status == "failed"
        assert reason == _tr.EXECUTION_FAILED
