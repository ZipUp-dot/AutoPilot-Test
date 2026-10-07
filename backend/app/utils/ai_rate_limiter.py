"""AI API 调用限流器 — 速率熔断（滑动窗口）+ 并发控制（Semaphore）

双层防护：
  1. 速率限制（Rate Limit）：每分钟最多 max_calls_per_min 次调用，超限熔断（acquire 返回 False）
  2. 并发限制（Concurrency Limit）：同一时刻最多 max_concurrency 个 AI 调用在途，
     由 BoundedSemaphore 排队控制（并发已满时等待，超时返回 False）

进程内共享单例（get_limiter）：代码生成、Vision 分析、自愈共用同一个限流器，
保证全局并发与速率上限一致 —— 无论多少线程/批次并发，AI 请求并发 ≤ AI_MAX_CONCURRENCY。
"""

import asyncio
import threading
import time
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger("autopilot.ai_limit")

# 并发槽等待超时（秒）：并发已满时最多排队等待该时长
CONCURRENCY_WAIT_SECONDS = 60.0


class AIRateLimiter:
    """滑动窗口限流 + 并发控制（进程内单例）"""

    def __init__(self, max_calls_per_min: int = 30, max_concurrency: int = 3) -> None:
        self._max_calls = max_calls_per_min
        self._max_concurrency = max_concurrency
        # 并发上限：同一时刻最多 max_concurrency 个调用在途
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        # 速率窗口（滑动 60s）
        self._calls: deque[float] = deque()
        self._total_calls = 0
        self._lock = threading.Lock()

    # ═══════════════════════════════════════════════
    # 速率限制（Rate Limit）
    # ═══════════════════════════════════════════════

    def acquire(self) -> bool:
        """获取一次速率额度（滑动窗口，线程安全）

        Returns:
            True 允许调用；False 触发熔断（本分钟内额度已用完）
        """
        with self._lock:
            now = time.time()
            # 清理 60 秒前的记录
            while self._calls and self._calls[0] < now - 60:
                self._calls.popleft()

            if len(self._calls) >= self._max_calls:
                logger.warning(
                    "AI 调用熔断触发：60 秒内已调用 %d 次（上限 %d），跳过本次调用",
                    len(self._calls), self._max_calls,
                )
                return False

            self._calls.append(now)
            self._total_calls += 1
            return True

    # ═══════════════════════════════════════════════
    # 并发控制（Concurrency Limit）
    # ═══════════════════════════════════════════════

    def acquire_slot(self, timeout: Optional[float] = None) -> bool:
        """获取并发槽位（Semaphore），并发已满时阻塞等待

        Args:
            timeout: 最长等待秒数；None 用 CONCURRENCY_WAIT_SECONDS 默认

        Returns:
            True 获取成功；False 等待超时
        """
        if timeout is None:
            timeout = CONCURRENCY_WAIT_SECONDS
        return self._semaphore.acquire(timeout=timeout)

    def release_slot(self) -> None:
        """释放并发槽位（必须与 acquire_slot 配对，通常置于 finally）"""
        self._semaphore.release()

    # ═══════════════════════════════════════════════
    # 原子预留（quota + slot）
    # ═══════════════════════════════════════════════

    def acquire_attempt(self, remaining: Optional[float] = None) -> Optional[str]:
        """原子预留一次 HTTP attempt 需要的 quota + slot。

        只有 quota 与 slot 都成功才进入 HTTP 阶段；任一失败本次 attempt 不成立：
          - quota 失败：不消耗 quota（窗口已满），本次不成立
          - slot 失败：回滚已预留的 quota，本次不成立
          - 成功：占用 1 quota + 1 slot（slot 由调用方在 HTTP 结束后的 finally
            release_slot 释放；backoff 睡眠不占用 slot）

        Args:
            remaining: 剩余预算（秒），用于 slot 等待上限；None 用默认

        Returns:
            None 表示预留成功；否则返回错误类型（"quota_timeout" | "slot_timeout"）
        """
        if not self.acquire():
            return "quota_timeout"
        if not self.acquire_slot(timeout=remaining):
            self._rollback_quota()
            return "slot_timeout"
        return None

    def _rollback_quota(self) -> None:
        """撤销一次刚消耗的 quota（用于 slot 获取失败时回滚预留）"""
        with self._lock:
            if self._calls:
                self._calls.pop()
            if self._total_calls > 0:
                self._total_calls -= 1

    # ═══════════════════════════════════════════════
    # 异步等槽（DEBT-SLOT-ASYNC）：等待期间不阻塞 event loop
    # ═══════════════════════════════════════════════

    async def _acquire_slot_async(self, timeout: Optional[float] = None) -> bool:
        """异步等槽实现：阻塞 acquire 放入默认 executor；取消后迟到获取立即自释放。"""
        if timeout is None:
            timeout = CONCURRENCY_WAIT_SECONDS

        loop = asyncio.get_running_loop()
        cancelled = asyncio.Event()

        def _blocking_acquire() -> bool:
            # 阻塞带 timeout：超时即返回，线程不会无限挂起
            acquired = self._semaphore.acquire(timeout=timeout)
            if acquired and cancelled.is_set():
                # 迟到获取：等待已被取消，立即自释放，杜绝槽泄漏（不变量 #9）
                self._semaphore.release()
                return False
            return acquired

        future = loop.run_in_executor(None, _blocking_acquire)
        try:
            return await future
        except asyncio.CancelledError:
            # 标记取消：executor 线程仍在阻塞等待，待其（迟到）获取时自释放
            cancelled.set()
            raise

    async def acquire_slot_async(self, timeout: Optional[float] = None) -> bool:
        """异步等槽：loop 不被阻塞；超时或取消后若迟到获取成功则立即自释放，杜绝泄漏。

        Args:
            timeout: 最长等待秒数；None 用 CONCURRENCY_WAIT_SECONDS 默认

        Returns:
            True 获取成功；False 等待超时
        """
        return await self._acquire_slot_async(timeout)

    async def acquire_attempt_async(self, remaining: Optional[float] = None) -> Optional[str]:
        """acquire_attempt 的异步版：quota 同步 + slot 异步等待 + 失败回滚。

        返回语义与同步版完全一致（None=成功 | "quota_timeout" | "slot_timeout"）。
        """
        if not self.acquire():
            return "quota_timeout"
        if not await self.acquire_slot_async(timeout=remaining):
            self._rollback_quota()
            return "slot_timeout"
        return None

    @property
    def active_count(self) -> int:
        """当前在途的 AI 调用数（监控/测试用）"""
        return self._max_concurrency - self._semaphore._value

    @property
    def max_concurrency(self) -> int:
        """并发上限"""
        return self._max_concurrency

    @property
    def recent_count(self) -> int:
        """当前窗口内的调用次数"""
        with self._lock:
            now = time.time()
            while self._calls and self._calls[0] < now - 60:
                self._calls.popleft()
            return len(self._calls)

    @property
    def total_calls(self) -> int:
        """进程启动以来的累计调用次数"""
        return self._total_calls


# ═══════════════════════════════════════════════
# 进程内共享单例
# 代码生成（ai_service）、Vision 分析（ai_service）、自愈（heal_service）
# 共用同一个限流器，保证全局并发与速率上限一致。
# ═══════════════════════════════════════════════

_limiter: Optional[AIRateLimiter] = None
_limiter_lock = threading.Lock()


def get_limiter() -> AIRateLimiter:
    """获取共享限流器（首次调用时按配置创建）"""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                from app.config import settings
                _limiter = AIRateLimiter(
                    max_calls_per_min=settings.AI_RATE_LIMIT,
                    max_concurrency=settings.AI_MAX_CONCURRENCY,
                )
    return _limiter
