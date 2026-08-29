"""执行状态管理 — 共享停止控制 + 重启恢复

只负责执行控制状态（停止标志）与孤儿任务恢复，不包含任何执行器逻辑。
AppiumService 和 PlaywrightService 共用同一套停止标志。
"""

import threading
from datetime import datetime, timedelta

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


def generate_worker_id() -> str:
    """生成执行 worker 标识（hostname:pid），用于区分哪个进程在跑任务"""
    import os
    import socket
    return f"{socket.gethostname()}:{os.getpid()}"


def recover_orphan_executions(db) -> int:
    """服务启动时恢复遗留执行状态

    Docker 重启后后台线程消失，数据库里 queued/running/healing 的任务不可能
    被当前进程续跑。仅当满足条件时才标记为 interrupted，避免误伤：
      - queued：线程从未启动即崩溃（无线程会执行它）→ interrupted
      - running / healing：heartbeat_at 缺失或超过超时阈值 → interrupted
        （心跳新鲜的 running 记录不会被误标）

    Returns:
        恢复（标记为 interrupted）的执行记录数量
    """
    from app.models.execution import Execution
    from app.config import settings

    stale_cutoff = datetime.utcnow() - timedelta(seconds=settings.EXECUTION_HEARTBEAT_TIMEOUT)

    recovered = 0
    rows = (
        db.query(Execution)
        .filter(Execution.status.in_(["queued", "running", "healing"]))
        .all()
    )
    for row in rows:
        if row.status == "queued":
            _mark_interrupted(row)
            recovered += 1
        elif row.heartbeat_at is None or row.heartbeat_at < stale_cutoff:
            _mark_interrupted(row)
            recovered += 1

    if recovered:
        db.commit()
    return recovered


def _mark_interrupted(row) -> None:
    """将遗留执行记录标记为 interrupted（服务异常导致未完成）"""
    row.status = "interrupted"
    row.end_time = datetime.utcnow()