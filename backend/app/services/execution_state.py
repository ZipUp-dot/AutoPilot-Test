"""执行状态管理 — 共享停止控制

只负责执行控制状态（停止标志），不包含任何执行器逻辑。
AppiumService 和 PlaywrightService 共用同一套停止标志。
"""

import threading

_stop_flags: dict[int, bool] = {}
_stop_lock = threading.Lock()


def set_stop_flag(execution_id: int) -> None:
    """设置停止标志"""
    with _stop_lock:
        _stop_flags[execution_id] = True


def clear_stop_flag(execution_id: int) -> None:
    """清除停止标志"""
    with _stop_lock:
        _stop_flags.pop(execution_id, None)


def is_stopped(execution_id: int) -> bool:
    """检查是否已停止"""
    with _stop_lock:
        return _stop_flags.get(execution_id, False)