"""AI API 调用限流器 — 滑动窗口熔断，防止自愈/生成无底线调用烧 Token"""

import time
import logging
from collections import deque

logger = logging.getLogger("autopilot.ai_limit")


class AIRateLimiter:
    """滑动窗口限流器（进程内单例）

    限制每分钟最多 max_calls_per_min 次调用；
    超限时 acquire() 返回 False，调用方应跳过本次 AI 调用（熔断）。
    """

    def __init__(self, max_calls_per_min: int = 30) -> None:
        self._max_calls = max_calls_per_min
        self._calls: deque[float] = deque()
        self._total_calls = 0

    def acquire(self) -> bool:
        """尝试获取一次调用额度

        Returns:
            True 允许调用；False 触发熔断（本分钟内额度已用完）
        """
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

    @property
    def recent_count(self) -> int:
        """当前窗口内的调用次数"""
        now = time.time()
        while self._calls and self._calls[0] < now - 60:
            self._calls.popleft()
        return len(self._calls)

    @property
    def total_calls(self) -> int:
        """进程启动以来的累计调用次数"""
        return self._total_calls
