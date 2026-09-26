"""P0-3 验收 — Limiter 原子预留语义

覆盖验收标准：
  4. 每次 attempt 各消耗 1 quota + 1 slot；backoff 期间 slot 已释放
  (quota_timeout/slot_timeout 语义：任一失败本次 attempt 不成立，不计 attempt_count、
   不占 slot、不耗 quota、不 backoff；slot 失败时回滚 quota 预留)
"""

import asyncio

import httpx
import pytest

from app.exceptions import AIException
from app.services.ai_service import _call_openai, _remaining, _make_deadline
from app.utils.ai_rate_limiter import AIRateLimiter


class TestAcquireAttemptAtomic:
    """acquire_attempt 原子预留：quota + slot"""

    def test_success_consumes_one_quota_and_holds_slot(self):
        limiter = AIRateLimiter(max_calls_per_min=5, max_concurrency=2)
        err = limiter.acquire_attempt(remaining=1.0)
        assert err is None
        # 1 quota + 1 slot 已被占用
        assert limiter.total_calls == 1
        assert limiter.recent_count == 1
        assert limiter.active_count == 1
        # 释放 slot（对应 finally）→ backoff 不占 slot
        limiter.release_slot()
        assert limiter.active_count == 0

    def test_quota_exhausted_does_not_consume_extra(self):
        limiter = AIRateLimiter(max_calls_per_min=1, max_concurrency=3)
        assert limiter.acquire() is True  # 占满唯一 quota
        err = limiter.acquire_attempt(remaining=0.0)
        assert err == "quota_timeout"
        # quota 未额外消耗；未占 slot
        assert limiter.total_calls == 1
        assert limiter.recent_count == 1
        assert limiter.active_count == 0

    def test_slot_failure_rolls_back_quota(self):
        limiter = AIRateLimiter(max_calls_per_min=5, max_concurrency=1)
        assert limiter.acquire_slot(timeout=0) is True  # 占满唯一 slot
        err = limiter.acquire_attempt(remaining=0.0)
        assert err == "slot_timeout"
        # quota 被回滚：窗口不残留本次预留
        assert limiter.total_calls == 0
        assert limiter.recent_count == 0
        # acquire_attempt 未新增 slot：仍只有测试自身持有的 1 个在途
        assert limiter.active_count == 1
        limiter.release_slot()
        assert limiter.active_count == 0

    def test_slot_failure_does_not_backoff(self):
        """slot 失败 → 本次 attempt 不成立，直接抛 slot_timeout，不进入 HTTP、不 backoff"""
        limiter = AIRateLimiter(max_calls_per_min=100, max_concurrency=1)
        assert limiter.acquire_slot(timeout=0) is True  # 占满唯一 slot
        err = limiter.acquire_attempt(remaining=0.0)
        assert err == "slot_timeout"
        # slot 未泄漏：仍只有测试自身持有的 1 个在途，acquire_attempt 未新增
        assert limiter.active_count == 1
        limiter.release_slot()
        assert limiter.active_count == 0

    def test_each_attempt_consumes_one_quota_one_slot_and_backoff_releases_slot(
        self, mock_settings, mocker
    ):
        """2 次 retryable 失败 + 1 次成功：消耗 3 quota+3 slot，backoff 期间 slot 已释放"""
        mock_settings("OPENAI_API_KEY", "test-key")
        limiter = "app.services.ai_service.ai_rate_limiter"
        from app.services import ai_service

        # 用独立的限流器实例隔离，避免污染共享单例计数
        isolated = AIRateLimiter(max_calls_per_min=100, max_concurrency=2)
        mocker.patch(limiter, isolated)

        call_count = [0]

        async def _post(*a, **kw):
            call_count[0] += 1
            # backoff 在 HTTP 返回后才发生；此处验证调用点
            if call_count[0] < 3:
                raise httpx.TimeoutException("timeout")
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]},
                request=httpx.Request("POST", "http://x"),
            )

        mock_client = mocker.AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = _post
        mocker.patch("app.services.ai_service.httpx.AsyncClient", return_value=mock_client)

        # 追踪 backoff 睡眠期间（等待返回前）的 active_count
        relevant = {}

        async def _tracking_sleep(*a, **kw):
            relevant["active_during_backoff"] = isolated.active_count
            return None
        mocker.patch.object(asyncio, "sleep", new=_tracking_sleep)

        result = _call_openai("prompt", "m", retries=3, remaining=8)
        assert result == "ok"
        assert call_count[0] == 3
        # 每次 HTTP attempt 消耗 1 quota + 1 slot
        assert isolated.total_calls == 3
        # backoff 期间 slot 已释放
        assert relevant["active_during_backoff"] == 0
        # 结束后无 slot 泄漏
        assert isolated.active_count == 0


class TestQuotaSlotErrorSemantics:
    """quota_timeout / slot_timeout → non-retryable，不计 KPI cohort"""

    def test_quota_timeout_raises_non_retryable(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "test-key")
        from app.services import ai_service
        isolated = AIRateLimiter(max_calls_per_min=1, max_concurrency=3)
        isolated.acquire()  # 占满 quota
        mocker.patch("app.services.ai_service.ai_rate_limiter", isolated)

        with pytest.raises(AIException) as ei:
            _call_openai("prompt", "m", remaining=0.5)
        assert ei.value.error_type == "quota_timeout"
        assert ei.value.retryable is False

    def test_slot_timeout_raises_non_retryable(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "test-key")
        from app.services import ai_service
        isolated = AIRateLimiter(max_calls_per_min=100, max_concurrency=1)
        # 占满唯一 slot → acquire_slot 立刻失败；让 deadline 保持充足，避免触发
        # remaining<=0 的 deadline 短路（那属于 deadline_exceeded，非本测试目标）
        isolated.acquire_slot(timeout=0)
        mocker.patch("app.services.ai_service.ai_rate_limiter", isolated)

        with pytest.raises(AIException) as ei:
            _call_openai("prompt", "m", remaining=8)
        assert ei.value.error_type == "slot_timeout"
        assert ei.value.retryable is False
        isolated.release_slot()