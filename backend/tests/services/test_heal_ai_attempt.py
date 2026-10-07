"""heal-v3 验收 — _ai_attempt 单次 attempt + Round 唯一重试权威 + 退避

冻结不变式覆盖：
  1. _ai_attempt 每次真实 HTTP 之前恰好一次成功的 acquire_attempt（唯一准入点）
  2. retryable 序列 → 单个 Round 内真实 HTTP ≤ MAX_HEAL_RETRY(3)（禁止 3×3 膨胀）
  3. 六类映射：429 / 5xx / 连接重置 / 超时 / non-retryable / 预算耗尽
  4. non-retryable 立即 break（不烧下一次 attempt）
  5. retryable 在 continue 之前发生退避
  6. Round 路径禁止调用 _call_heal_ai（私有客户端冻结）
"""

import asyncio
import json
import time
from datetime import datetime as dt

from app.config import settings
from app.exceptions import DeadlineExceeded
from app.services.ai_service import _HTTPAttempt
from app.services.heal_service import HealRoundService
from app.utils.step_canonicalizer import hash_steps


# ═══════════════════════════════════════════════
# 测试脚手架
# ═══════════════════════════════════════════════

class _LimiterSpy:
    """记录 acquire_attempt / release_slot 调用顺序；admission 控制预留结果"""

    def __init__(self, events=None, admission=None):
        self.events = events if events is not None else []
        self.acquire_attempt_calls = []
        self.release_slot_calls = 0
        self._admission = admission

    def acquire_attempt(self, remaining=None):
        self.events.append("acquire")
        self.acquire_attempt_calls.append(remaining)
        return self._admission

    async def acquire_attempt_async(self, remaining=None):
        # DEBT-SLOT-ASYNC：生产入口改为异步版；复用同一记录路径，语义与同步版一致
        return self.acquire_attempt(remaining)

    def release_slot(self):
        self.events.append("release")
        self.release_slot_calls += 1


def _http_mock(mocker, items, events=None):
    """按顺序返回 _HTTPAttempt，或 raise 传入的 Exception；每次调用记录 'http' 事件"""
    seq = list(items)

    async def _call(**kwargs):
        if events is not None:
            events.append("http")
        item = seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return mocker.patch("app.services.ai_service._chat_http_attempt", side_effect=_call)


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


def _run_round(db, exec_obj, case, project, page):
    return asyncio.run(HealRoundService(db).heal_case(
        execution_id=exec_obj.id,
        case_id=case.id,
        project_id=project.id,
        page=page,
        platform="web",
    ))


def _stub_round_context(mocker):
    """收敛 Round 上下文，使测试只暴露 AI attempt 机制"""
    mocker.patch(
        "app.services.heal_service.HealService._capture_failure_context",
        new=mocker.AsyncMock(return_value={}),
    )
    mocker.patch(
        "app.services.heal_service.HealService._build_heal_prompt",
        return_value="PROMPT",
    )


# ═══════════════════════════════════════════════
# 1. _ai_attempt 六类映射（单次 attempt 语义）
# ═══════════════════════════════════════════════

class TestAIAttemptMapping:
    """_ai_attempt 直接调用：错误类型映射 + 准入/释放配对"""

    def _svc(self):
        return HealRoundService(None)

    def _run(self, mocker, attempt, events=None, admission=None):
        events = events if events is not None else []
        spy = _LimiterSpy(events=events, admission=admission)
        mocker.patch("app.services.heal_service.ai_rate_limiter", spy)
        _http_mock(mocker, [attempt], events=events)
        return asyncio.run(
            self._svc()._ai_attempt("PROMPT", "web", 300.0, 1, time.monotonic() + 300)
        ), spy, events

    def test_success_maps_to_ok(self, mock_settings, mocker):
        """成功：error_type=None → (True, content, None)"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        events = []
        result, spy, _ = self._run(
            mocker, _HTTPAttempt(content="CODE", error_type=None), events=events
        )
        assert result == (True, "CODE", None)
        # 唯一准入点：HTTP 前恰好一次 acquire，HTTP 后恰好一次 release
        assert events == ["acquire", "http", "release"]
        assert len(spy.acquire_attempt_calls) == 1
        assert spy.release_slot_calls == 1

    def test_429_maps_to_rate_limited(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "sk-test")
        events = []
        result, spy, _ = self._run(
            mocker, _HTTPAttempt(error_type="rate_limited", retryable=True), events=events
        )
        assert result == (False, None, "rate_limited")
        assert events == ["acquire", "http", "release"]

    def test_5xx_maps_to_server_error(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "sk-test")
        result, _, _ = self._run(
            mocker, _HTTPAttempt(error_type="server_error", retryable=True)
        )
        assert result == (False, None, "server_error")

    def test_connect_reset_maps_to_connect_error(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "sk-test")
        result, _, _ = self._run(
            mocker, _HTTPAttempt(error_type="connect_error", retryable=True)
        )
        assert result == (False, None, "connect_error")

    def test_timeout_maps_to_read_timeout(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "sk-test")
        result, _, _ = self._run(
            mocker, _HTTPAttempt(error_type="read_timeout", retryable=True)
        )
        assert result == (False, None, "read_timeout")

    def test_non_retryable_maps_to_http_4xx(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "sk-test")
        result, _, _ = self._run(
            mocker, _HTTPAttempt(error_type="http_4xx", retryable=False)
        )
        assert result == (False, None, "http_4xx")

    def test_budget_exhausted_skips_http(self, mock_settings, mocker):
        """remaining <= 0 → deadline_exceeded，且不占用任何 quota/slot"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        spy = _LimiterSpy()
        mocker.patch("app.services.heal_service.ai_rate_limiter", spy)
        http = _http_mock(mocker, [_HTTPAttempt(content="X")])
        result = asyncio.run(
            self._svc()._ai_attempt("PROMPT", "web", 0.0, 1, __import__("time").monotonic())
        )
        assert result == (False, None, "deadline_exceeded")
        http.assert_not_called()
        assert spy.acquire_attempt_calls == []

    def test_deadline_exceeded_exception_maps(self, mock_settings, mocker):
        """共享层 wait_for 到期抛 DeadlineExceeded → deadline_exceeded（仍释放 slot）"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        events = []
        result, spy, _ = self._run(mocker, DeadlineExceeded(), events=events)
        assert result == (False, None, "deadline_exceeded")
        assert events == ["acquire", "http", "release"]

    def test_admission_failure_returns_ai_request_failed(self, mock_settings, mocker):
        """quota/slot 预留失败 → 不发起 HTTP，返回 ai_request_failed"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        spy = _LimiterSpy(admission="quota_timeout")
        mocker.patch("app.services.heal_service.ai_rate_limiter", spy)
        http = _http_mock(mocker, [_HTTPAttempt(content="X")])
        result = asyncio.run(
            self._svc()._ai_attempt("PROMPT", "web", 300.0, 1, __import__("time").monotonic() + 300)
        )
        assert result == (False, None, "ai_request_failed")
        http.assert_not_called()
        assert spy.release_slot_calls == 0  # 未占 slot，不得释放


# ═══════════════════════════════════════════════
# 2. Round 循环：唯一重试权威 + 退避 + 预算 ≤3
# ═══════════════════════════════════════════════

class TestRoundRetryAuthority:
    """Round 循环决定是否重试；真实 HTTP ≤ MAX_HEAL_RETRY"""

    def _patch_sleep(self, mocker):
        return mocker.patch("app.services.heal_service.asyncio.sleep", new=mocker.AsyncMock())

    def test_retryable_sequence_caps_http_at_max_retry(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mock_settings, mocker,
    ):
        """3 次 retryable 失败 → Round 内真实 HTTP 恰好 3（不膨胀为 9），终态 ai_request_failed"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        exec_obj, _ = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        _stub_round_context(mocker)

        events = []
        spy = _LimiterSpy(events=events)
        mocker.patch("app.services.heal_service.ai_rate_limiter", spy)
        http = _http_mock(mocker, [
            _HTTPAttempt(error_type="rate_limited", retryable=True),
            _HTTPAttempt(error_type="server_error", retryable=True),
            _HTTPAttempt(error_type="connect_error", retryable=True),
        ], events=events)
        self._patch_sleep(mocker)

        result = _run_round(db_session, exec_obj, sample_test_case, sample_project, _mock_page(mocker))

        assert http.call_count == settings.MAX_HEAL_RETRY
        assert result.retry_status == "failed"
        assert result.error_type == "ai_request_failed"
        # 每次 HTTP 之前恰好一次成功的 acquire_attempt（顺序 + 配对）
        assert events == [
            "acquire", "http", "release",
            "acquire", "http", "release",
            "acquire", "http", "release",
        ]

    def test_retryable_backoff_before_each_continue(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mock_settings, mocker,
    ):
        """retryable 失败后先退避再消耗下一次 attempt（指数 + jitter）"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        exec_obj, _ = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        _stub_round_context(mocker)
        mocker.patch("app.services.heal_service.ai_rate_limiter", _LimiterSpy())
        _http_mock(mocker, [
            _HTTPAttempt(error_type="rate_limited", retryable=True),
            _HTTPAttempt(error_type="server_error", retryable=True),
            _HTTPAttempt(error_type="rate_limited", retryable=True),
        ])
        sleep_mock = self._patch_sleep(mocker)

        _run_round(db_session, exec_obj, sample_test_case, sample_project, _mock_page(mocker))

        sleeps = [c.args[0] for c in sleep_mock.call_args_list]
        assert len(sleeps) == settings.MAX_HEAL_RETRY - 1  # 3 次 attempt → 2 次退避
        assert 1.0 <= sleeps[0] <= 1.5      # 2**0 + U(0,0.5)
        assert 2.0 <= sleeps[1] <= 2.5      # 2**1 + U(0,0.5)
        assert sleeps[0] < sleeps[1]        # 指数递增

    def test_non_retryable_breaks_immediately(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mock_settings, mocker,
    ):
        """non-retryable（http_4xx）→ 单次 HTTP 即 break，不再退避/消耗 attempt"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        exec_obj, _ = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        _stub_round_context(mocker)
        mocker.patch("app.services.heal_service.ai_rate_limiter", _LimiterSpy())
        http = _http_mock(mocker, [
            _HTTPAttempt(error_type="http_4xx", retryable=False),
            _HTTPAttempt(error_type="http_4xx", retryable=False),
            _HTTPAttempt(error_type="http_4xx", retryable=False),
        ])
        sleep_mock = self._patch_sleep(mocker)

        result = _run_round(db_session, exec_obj, sample_test_case, sample_project, _mock_page(mocker))

        assert http.call_count == 1
        sleep_mock.assert_not_called()
        assert result.retry_status == "failed"
        assert result.error_type == "ai_request_failed"

    def test_budget_exhausted_closes_as_deadline(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mock_settings, mocker,
    ):
        """Round 预算耗尽（HTTP 层 DeadlineExceeded）→ 基础设施故障收口 deadline_exceeded"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        exec_obj, _ = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        _stub_round_context(mocker)
        mocker.patch("app.services.heal_service.ai_rate_limiter", _LimiterSpy())
        http = _http_mock(mocker, [DeadlineExceeded()])
        self._patch_sleep(mocker)

        result = _run_round(db_session, exec_obj, sample_test_case, sample_project, _mock_page(mocker))

        assert http.call_count == 1
        assert result.retry_status == "failed"
        assert result.error_type == "deadline_exceeded"
        assert result.upgrade_execution_failed is True

    def test_round_never_calls_private_heal_ai(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mock_settings, mocker,
    ):
        """冻结：Round 路径禁止调用 _call_heal_ai（私有 httpx 客户端）"""
        mock_settings("OPENAI_API_KEY", "sk-test")
        exec_obj, _ = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        _stub_round_context(mocker)
        mocker.patch("app.services.heal_service.ai_rate_limiter", _LimiterSpy())
        _http_mock(mocker, [_HTTPAttempt(error_type="server_error", retryable=True)])
        self._patch_sleep(mocker)
        private = mocker.patch(
            "app.services.heal_service.HealService._call_heal_ai",
            side_effect=AssertionError("Round 路径不得调用 _call_heal_ai"),
        )

        _run_round(db_session, exec_obj, sample_test_case, sample_project, _mock_page(mocker))

        private.assert_not_called()
