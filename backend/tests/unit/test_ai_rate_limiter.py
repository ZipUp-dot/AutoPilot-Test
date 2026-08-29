"""AIRateLimiter 并发控制（Semaphore）单元测试 — 阶段 8

验证：并发上限（Concurrency Limit）+ 速率（Rate Limit）双层防护
"""

import threading
import time

import httpx
import pytest

from app.utils.ai_rate_limiter import AIRateLimiter


class TestConcurrencySemaphore:
    """并发控制（Semaphore）"""

    def test_concurrency_limits_active_calls(self):
        """同时获取并发槽的线程数不超过 max_concurrency"""
        limiter = AIRateLimiter(max_calls_per_min=10000, max_concurrency=3)
        lock = threading.Lock()
        active = [0]
        peak = [0]
        errors = []

        def worker():
            if not limiter.acquire_slot(timeout=5):
                errors.append("slot timeout")
                return
            try:
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.05)
                with lock:
                    active[0] -= 1
            finally:
                limiter.release_slot()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发槽排队超时: {errors}"
        assert peak[0] <= 3, f"并发峰值 {peak[0]} 超过上限 3"

    def test_concurrency_waits_for_release(self):
        """并发已满时新请求阻塞等待，释放后获取成功"""
        limiter = AIRateLimiter(max_calls_per_min=10000, max_concurrency=1)
        got = []

        def holder():
            assert limiter.acquire_slot(timeout=5)
            time.sleep(0.3)
            limiter.release_slot()

        def waiter():
            got.append(limiter.acquire_slot(timeout=5))
            limiter.release_slot()

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)
        t1.start()
        time.sleep(0.05)  # 确保 t1 先占用唯一槽位
        t2.start()
        t1.join()
        t2.join()

        assert got == [True], f"等待后应成功获取槽位: {got}"

    def test_slot_timeout_returns_false(self):
        """并发已满且等待超时 → 返回 False（不阻塞永久）"""
        limiter = AIRateLimiter(max_calls_per_min=10000, max_concurrency=1)
        assert limiter.acquire_slot(timeout=0) is True
        assert limiter.acquire_slot(timeout=0.05) is False
        limiter.release_slot()

    def test_active_count_tracks_inflight(self):
        """active_count 反映当前在途调用数"""
        limiter = AIRateLimiter(max_calls_per_min=10000, max_concurrency=2)
        assert limiter.active_count == 0
        limiter.acquire_slot(timeout=0)
        assert limiter.active_count == 1
        limiter.acquire_slot(timeout=0)
        assert limiter.active_count == 2
        limiter.release_slot()
        assert limiter.active_count == 1
        limiter.release_slot()
        assert limiter.active_count == 0

    def test_rate_acquire_thread_safe(self):
        """速率窗口在多线程并发下不超发（锁保护）"""
        limiter = AIRateLimiter(max_calls_per_min=50, max_concurrency=100)
        results = []

        def worker():
            results.append(limiter.acquire())

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 50, f"应恰好 50 次成功，实际 {sum(results)}"


class TestSharedLimiter:
    """进程内共享单例"""

    def test_ai_and_heal_share_same_limiter(self):
        """代码生成与自愈共用同一限流器（全局并发/速率上限一致）"""
        from app.services.ai_service import ai_rate_limiter as ai_limiter
        from app.services.heal_service import ai_rate_limiter as heal_limiter

        assert ai_limiter is heal_limiter

    def test_get_limiter_returns_shared_instance(self):
        from app.utils.ai_rate_limiter import get_limiter
        from app.services.ai_service import ai_rate_limiter as ai_limiter

        assert get_limiter() is ai_limiter
        assert get_limiter().max_concurrency >= 1
        assert get_limiter().max_concurrency == ai_limiter.max_concurrency


class TestBatchConcurrencyEndToEnd:
    """批量 100 任务端到端验收（经 _call_openai 真实调用链）

    验收标准：并发数不超配置、速率限制生效、任务最终全部完成、失败不无限重试
    """

    def _reset_limiter(self):
        """恢复共享限流器到默认窗口（速率上限与窗口清空）"""
        from app.config import settings
        from app.services.ai_service import ai_rate_limiter
        ai_rate_limiter._max_calls = settings.AI_RATE_LIMIT
        ai_rate_limiter._calls.clear()

    def _mock_httpx_success(self, mocker, active, peak, lock):
        """构造成功响应，跟踪 HTTP 在途峰值"""
        from app.services import ai_service

        class _FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.01)
                with lock:
                    active[0] -= 1
                return _FakeResponse()

        mocker.patch.object(ai_service.httpx, "Client", _FakeClient)
        mocker.patch.object(ai_service.time, "sleep")

    def test_100_concurrent_calls_rate_and_concurrency(self, mock_settings, mocker):
        """100 并发：速率熔断恰好 N 次放行，HTTP 并发峰值 ≤ AI_MAX_CONCURRENCY，全部结束无死锁"""
        from app.services import ai_service
        from app.exceptions import AIException

        limiter = ai_service.ai_rate_limiter
        mock_settings("OPENAI_API_KEY", "test-key")
        limiter._calls.clear()
        limiter._max_calls = 20  # 临时把速率上限降到 20（默认 30）
        rate_limit = limiter._max_calls

        lock = threading.Lock()
        active, peak = [0], [0]
        successes, breakers = [0], [0]
        errors = []
        self._mock_httpx_success(mocker, active, peak, lock)

        def worker():
            try:
                ai_service._call_openai("test prompt", "gpt-4o")
                successes[0] += 1
            except AIException:
                breakers[0] += 1
            except Exception as e:  # noqa: BLE001 — 捕获一切以断言无意外异常
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        try:
            # 速率限制生效：恰好 20 次放行，其余 80 次熔断
            assert successes[0] == rate_limit, f"放行 {successes[0]} != {rate_limit}"
            assert breakers[0] == 100 - rate_limit, f"熔断 {breakers[0]} != {100 - rate_limit}"
            # 并发数不超过配置
            assert peak[0] <= limiter.max_concurrency, \
                f"HTTP 并发峰值 {peak[0]} 超过配置 {limiter.max_concurrency}"
            # 全部结束且无异常（无死锁 / 槽位无泄漏）
            assert not errors, f"意外异常: {errors}"
            assert limiter.active_count == 0, f"并发槽位泄漏: active_count={limiter.active_count}"
        finally:
            self._reset_limiter()

    def test_failure_does_not_retry_infinitely_and_releases_slot(self, mock_settings, mocker):
        """失败重试有上限（retries=3）且 finally 释放并发槽位，不无限重试、不耗尽 Semaphore"""
        from app.services import ai_service
        from app.exceptions import AIException

        limiter = ai_service.ai_rate_limiter
        mock_settings("OPENAI_API_KEY", "test-key")
        limiter._calls.clear()

        call_count = [0]

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **kw):
                call_count[0] += 1
                raise httpx.TimeoutException("timeout")

        mocker.patch.object(ai_service.httpx, "Client", _FakeClient)
        mocker.patch.object(ai_service.time, "sleep")

        try:
            with pytest.raises(AIException, match="已重试3次"):
                ai_service._call_openai("test prompt", "gpt-4o")
            # 有界重试：恰好 retries=3 次 HTTP 尝试，不无限重试
            assert call_count[0] == 3, f"HTTP 尝试 {call_count[0]} 次，应为 3 次"
            # 失败后槽位已释放：Semaphore 未耗尽，后续任务仍可获取
            assert limiter.active_count == 0, f"失败后并发槽位泄漏: active_count={limiter.active_count}"
            assert limiter.acquire_slot(timeout=0) is True
            limiter.release_slot()
        finally:
            limiter._calls.clear()
            self._reset_limiter()
