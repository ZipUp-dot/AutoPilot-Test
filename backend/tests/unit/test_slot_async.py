"""DEBT-SLOT-ASYNC 验收测试 — slot 获取异步化（AC-01 / AC-03 / AC-05）

对应 Spec：docs/SPEC-DEBT-SLOT-ASYNC-v1.md §12（Acceptance Tests）。
约束（Spec §14 D-2）：不 patch 全局 asyncio.sleep，仅以真实协程协作观察调度行为。
"""

import asyncio
import time

import pytest

from app.utils.ai_rate_limiter import AIRateLimiter


async def test_wait_does_not_block_loop():
    """AC-01：槽满等待期间 event loop 保持可调度 —— 标记协程须在 ≤0.1s 内被调度。

    设计要点：计时从「waiter 已排队」起算。若实现为同步阻塞（在协程内直接
    semaphore.acquire），waiter 会独占 loop，标记协程被推迟到槽释放后才执行，
    elapsed 将 ≈ waiter timeout → 断言失败；异步实现下 elapsed ≈ 0。
    """
    limiter = AIRateLimiter(max_calls_per_min=100, max_concurrency=1)
    assert limiter.acquire_slot(timeout=0) is True  # 占满唯一槽

    marker_ran = []

    async def _marker():
        marker_ran.append(time.monotonic())

    waiter = asyncio.create_task(limiter.acquire_slot_async(timeout=1.0))
    t0 = time.monotonic()
    await asyncio.wait_for(_marker(), timeout=0.1)
    marker_elapsed = time.monotonic() - t0

    assert marker_ran, "标记协程从未被调度 → event loop 被 slot 等待阻塞"
    assert marker_elapsed <= 0.1, f"标记协程延迟 {marker_elapsed:.3f}s → loop 被阻塞"

    # 释放槽 → waiter 应取得（无泄漏）
    limiter.release_slot()
    assert await asyncio.wait_for(waiter, timeout=2.0) is True
    limiter.release_slot()  # 归还 waiter 持有的槽
    assert limiter.active_count == 0


async def test_cancelled_wait_releases_late_acquire():
    """AC-03：取消 wait 后，迟到获取必须自释放 —— active_count 回到基线，无槽泄漏。"""
    limiter = AIRateLimiter(max_calls_per_min=100, max_concurrency=1)
    assert limiter.acquire_slot(timeout=0) is True  # 占满唯一槽
    assert limiter.active_count == 1  # 基线

    waiter = asyncio.create_task(limiter.acquire_slot_async(timeout=2.0))
    await asyncio.sleep(0.05)  # 让 waiter 进入 semaphore 等待
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # 强制槽可用 → 触发「迟到获取」；实现须自释放，不得泄漏
    limiter.release_slot()
    await asyncio.sleep(0.3)  # 给 executor 线程完成迟到获取与自释放

    assert limiter.active_count == 0, "迟到获取未自释放 → 槽泄漏"


async def test_wait_respects_deadline():
    """AC-05：slot 等待上限 = remaining；槽满 → slot_timeout 且墙钟 ≈ remaining。"""
    limiter = AIRateLimiter(max_calls_per_min=100, max_concurrency=1)
    assert limiter.acquire_slot(timeout=0) is True  # 占满唯一槽

    t0 = time.monotonic()
    err = await limiter.acquire_attempt_async(remaining=0.2)
    elapsed = time.monotonic() - t0

    assert err == "slot_timeout"
    assert 0.15 <= elapsed < 0.6, f"墙钟 {elapsed:.3f}s 与 remaining=0.2s 不符"
    # quota 已回滚，本次 attempt 不成立
    assert limiter.total_calls == 0
    assert limiter.recent_count == 0
    # 无泄漏：仍只有测试自身持有的 1 个
    assert limiter.active_count == 1
    limiter.release_slot()
    assert limiter.active_count == 0
