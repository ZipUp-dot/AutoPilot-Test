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
    """检查是否已停止（内存 fast-path，非权威；决策点必须查 DB stop_requested_at）"""
    with _stop_lock:
        return _stop_flags.get(execution_id, False)


def db_stop_requested(db, execution_id: int) -> bool:
    """DB 权威 stop_requested_at 判定（决策点必须查 DB，内存 flag 仅 fast-path）。

    stop_requested_at 是唯一权威：非 NULL 即表示 Stop 已被请求。禁止仅凭内存
    flag 做 Recovery / 决策点判断。
    """
    from app.models.execution import Execution
    row = db.query(Execution).filter(Execution.id == execution_id).first()
    if row is None:
        return False
    return row.stop_requested_at is not None


# ── Execution-level 线性化边界 ──
# Stop / Recovery / ExecutionFinalizer / queued→running 条件启动 / Heal Phase B 全部
# 对同一 Execution 使用同一 per-execution lock（SQLite 进程内；MySQL 另有 SELECT
# ... FOR UPDATE）。谁先拿到线性化点谁建立权威事实，其他操作基于最新状态重算。
_EXEC_LOCKS: dict[int, threading.Lock] = {}
_EXEC_LOCKS_GUARD = threading.Lock()


def get_execution_lock(execution_id: int) -> threading.Lock:
    """返回该 Execution 的 process-local 串行化锁（防 dict 并发扩容）"""
    with _EXEC_LOCKS_GUARD:
        lock = _EXEC_LOCKS.get(execution_id)
        if lock is None:
            lock = threading.Lock()
            _EXEC_LOCKS[execution_id] = lock
        return lock


def generate_worker_id() -> str:
    """生成执行 worker 标识（hostname:pid），用于区分哪个进程在跑任务"""
    import os
    import socket
    return f"{socket.gethostname()}:{os.getpid()}"


def recover_orphan_executions(db) -> int:
    """服务启动时恢复遗留执行状态

    Docker 重启后后台线程消失，数据库里 queued/running/healing 的任务不可能
    被当前进程续跑。仅当满足条件时才标记为终态，避免误伤：
      - 【先查 stop_requested_at】非 NULL = 用户 Stop 意图 → stopped 路径
        （在途 case→failed(recovered_after_stop)，无真实失败证据时 terminal_reason=
        user_stopped，不得因崩溃改判 interrupted 否则 KPI 误排；已有具体失败证据则
        保留原语义）；剩余 pending→skipped(user_stopped)；Execution=stopped。
      - queued（无 Stop）：线程从未启动即崩溃 → interrupted
      - running / healing（无 Stop）：heartbeat_at 缺失或超过超时阈值 → interrupted
        （心跳新鲜的 running 记录不会被误标）

    收敛统一经 ExecutionFinalizer（seal / seal_stopped），禁止直接写
    Execution.status（否则半终态泄漏）。

    Returns:
        恢复（标记为终态）的执行记录数量
    """
    from app.models.execution import Execution
    from app.config import settings
    from app.services.execution_finalizer import ExecutionFinalizer
    from app.utils import terminal_reason as _tr

    stale_cutoff = datetime.utcnow() - timedelta(seconds=settings.EXECUTION_HEARTBEAT_TIMEOUT)

    recovered = 0
    rows = (
        db.query(Execution)
        .filter(Execution.status.in_(["queued", "running", "healing"]))
        .all()
    )
    for row in rows:
        # P0-9：stop_requested_at 是唯一权威，先于一切状态判断（Recovery 决策点必查 DB）
        if row.stop_requested_at is not None:
            # 在途 case 需标记 recovered_after_stop（error_type 层；terminal_reason 仍 user_stopped）
            ExecutionFinalizer(db).seal_stopped(row.id, recovered=True)
            recovered += 1
        elif row.status == "queued":
            ExecutionFinalizer(db).seal(row.id, "interrupted", _tr.INTERRUPTED)
            recovered += 1
        elif row.heartbeat_at is None or row.heartbeat_at < stale_cutoff:
            ExecutionFinalizer(db).seal(row.id, "interrupted", _tr.INTERRUPTED)
            recovered += 1

    if recovered:
        db.commit()
    return recovered