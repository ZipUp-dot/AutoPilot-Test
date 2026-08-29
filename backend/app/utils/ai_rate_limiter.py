"""AI API 调用限流器 — 速率熔断（滑动窗口）+ 并发控制（Semaphore）

双层防护：
  1. 速率限制（Rate Limit）：每分钟最多 max_calls_per_min 次调用，超限熔断（acquire 返回 False）
  2. 并发限制（Concurrency Limit）：同一时刻最多 max_concurrency 个 AI 调用在途，
     由 BoundedSemaphore 排队控制（并发已满时等待，超时返回 False）

进程内共享单例（get_limiter）：代码生成、Vision 分析、自愈共用同一个限流器，
保证全局并发与速率上限一致 —— 无论多少线程/批次并发，AI 请求并发 ≤ AI_MAX_CONCURRENCY。
"""

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

    def acquire_slot(self, timeout: float = CONCURRENCY_WAIT_SECONDS) -> bool:
        """获取并发槽位（Semaphore），并发已满时阻塞等待

        Returns:
            True 获取成功；False 等待超时
        """
        return self._semaphore.acquire(timeout=timeout)

    def release_slot(self) -> None:
        """释放并发槽位（必须与 acquire_slot 配对，通常置于 finally）"""
        self._semaphore.release()

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
