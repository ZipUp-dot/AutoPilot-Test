"""P0-3 验收 — AI 重试分类 / Retry-After / 超时分项压缩 / 结构化日志

覆盖验收标准：
  1. 429 带 Retry-After → 尊重该头；Retry-After=30 且 remaining 只有 8 → 只等 min(30, rem)
  2. 401/400 → 不重试，总耗时 <1s
  6. 成功/失败调用均有结构化日志行
  3. remaining=5 时 read timeout 被压到 ≤5s，不会跑满 120s（_request_timeout 分项压缩）
"""

import asyncio
import logging
import time

import httpx
import pytest

from app.exceptions import AIException, DeadlineExceeded
from app.services.ai_service import (
    _call_openai,
    _chat_http_attempt,
    _make_deadline,
    _remaining,
    _request_timeout,
    _retry_wait,
)


def _async_client_for(mocker, resp):
    """构建返回给定 httpx.Response 的异步 AsyncClient mock，可数 post 调用次数。"""
    calls = [0]
    async def _post(*a, **kw):
        calls[0] += 1
        return resp
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = _post
    mocker.patch("app.services.ai_service.httpx.AsyncClient", return_value=mock_client)
    return calls


class TestRetryAfter:
    """验收 #1：429 + Retry-After 尊重 + remaining 钳制"""

    def test_429_is_retryable_and_retry_after_captured(self, mocker):
        """429 → error_type=rate_limited, retryable=True, retry_after 取自头"""
        resp = httpx.Response(
            429, request=httpx.Request("POST", "http://x"),
            headers={"Retry-After": "30"},
        )
        _async_client_for(mocker, resp)
        attempt = asyncio.run(_chat_http_attempt(
            model="m", messages=[], max_tokens=1, attempt=1,
            deadline=_make_deadline(None),
        ))
        assert attempt.error_type == "rate_limited"
        assert attempt.retryable is True
        assert attempt.retry_after == "30"

    def test_retry_wait_clamps_to_remaining(self, mocker):
        """Retry-After=30 且 remaining≈8 → 只等 8s，不等到 30s"""
        deadline = _make_deadline(8.0)
        remaining = _remaining(deadline)
        assert 0 < remaining <= 8.0
        wait = _retry_wait(attempt=1, retry_after="30", deadline=deadline)
        assert wait == pytest.approx(remaining, abs=1.0)
        assert wait <= remaining + 1e-6

    def test_retry_wait_respects_retry_after_when_smaller(self):
        """Retry-After=5 且 remaining 充足 → 等 5s，尊重该头"""
        deadline = _make_deadline(60.0)
        wait = _retry_wait(attempt=1, retry_after="5", deadline=deadline)
        assert wait == pytest.approx(5.0, abs=1e-6)

    def test_invalid_retry_after_falls_back_to_backoff(self):
        """Retry-After 无法解析 → 回退指数退避公式"""
        deadline = _make_deadline(60.0)
        wait = _retry_wait(attempt=1, retry_after="not-a-number", deadline=deadline)
        assert 1.0 <= wait <= 1.5  # base*1 + jitter(0~0.5)


class TestNonRetryable:
    """验收 #2：401/400 不重试，总耗时 <1s"""

    @pytest.mark.parametrize("status", [401, 400])
    def test_4xx_no_retry_fast(self, mock_settings, mocker, status):
        mock_settings("OPENAI_API_KEY", "test-key")
        resp = httpx.Response(status, request=httpx.Request("POST", "http://x"))
        calls = _async_client_for(mocker, resp)
        # 消除退避可能产生的 sleep 时长（本场景应不触发，兜底确保 <1s）
        async def _noop_sleep(*a, **kw):
            return None
        mocker.patch.object(asyncio, "sleep", new=_noop_sleep)

        start = time.perf_counter()
        with pytest.raises(AIException) as ei:
            _call_openai("prompt", "m", remaining=8)
        elapsed = time.perf_counter() - start

        assert ei.value.error_type == "http_4xx"
        assert ei.value.retryable is False
        # 4xx 不重试：恰好 1 次 HTTP 尝试
        assert calls[0] == 1
        assert elapsed < 1.0

    def test_5xx_is_retryable(self, mock_settings, mocker):
        """5xx → retryable=True（真正重试）"""
        mock_settings("OPENAI_API_KEY", "test-key")
        resp = httpx.Response(503, request=httpx.Request("POST", "http://x"))
        calls = _async_client_for(mocker, resp)

        async def _noop_sleep(*a, **kw):
            return None
        mocker.patch.object(asyncio, "sleep", new=_noop_sleep)

        with pytest.raises(AIException) as ei:
            _call_openai("prompt", "m", retries=2, remaining=8)
        assert ei.value.error_type == "server_error"
        assert ei.value.retryable is True
        # 5xx 重试至上限：retries=2 次 HTTP 尝试
        assert calls[0] == 2


class TestReadTimeoutCompression:
    """验收 #3：remaining 很小 → read timeout 被压到 ≤ remaining，不跑满 120s"""

    def test_request_timeout_read_bounded_by_remaining(self):
        deadline = _make_deadline(5.0)
        timeout = _request_timeout(deadline)
        assert timeout.read == pytest.approx(5.0, abs=0.2)
        assert timeout.read < 120.0  # 不跑满默认 120s

    def test_request_timeout_short_circuits_when_no_remaining(self):
        deadline = time.monotonic()  # remaining<=0
        timeout = _request_timeout(deadline)
        assert timeout.read <= 1e-6


class TestStructuredLog:
    """验收 #6：成功/失败调用均有结构化日志行"""

    def _do_attempt(self, mocker, resp, caplog):
        _async_client_for(mocker, resp)
        caplog.set_level(logging.INFO, logger="autopilot.ai")
        attempt = asyncio.run(_chat_http_attempt(
            model="m", messages=[], max_tokens=1, attempt=2,
            deadline=_make_deadline(None),
            batch_id="b1", case_id="c1",
        ))
        return attempt

    def test_success_logs_structured_line(self, mocker, caplog):
        resp = httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}],
                       "usage": {"total_tokens": 7}},
            request=httpx.Request("POST", "http://x"),
        )
        attempt = self._do_attempt(mocker, resp, caplog)
        assert attempt.content == "ok"
        body = caplog.text
        assert "AI attempt batch=b1 case=c1 attempt=2" in body
        assert "latency_ms=" in body
        assert "usage=7" in body

    def test_failure_logs_structured_line(self, mocker, caplog):
        resp = httpx.Response(
            429, request=httpx.Request("POST", "http://x"),
            headers={"Retry-After": "30"},
        )
        attempt = self._do_attempt(mocker, resp, caplog)
        assert attempt.error_type == "rate_limited"
        body = caplog.text
        assert "AI attempt batch=b1 case=c1 attempt=2" in body
        assert "error_type=rate_limited" in body
        assert "http_status=429" in body
        assert "latency_ms=" in body
        assert "retry_after=30" in body