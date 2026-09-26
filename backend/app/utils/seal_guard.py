"""Seal 守卫 — 终态 Execution 禁止任何后续写操作

P0-10：Execution 进入终态（completed/stopped/failed/interrupted）即封存，
终态 status 就是数据库封存边界（无需独立 sealed/is_sealed 字段）。此后
Manifest / Runtime / Step / HealRecord 只读；任何修改尝试由 guard_not_sealed
抛出 SealedExecutionError(409)。

接入点（关键写路径，禁止遗漏）：
  - 执行器每 case 写入前（playwright _execute_case / appium _execute_sync）
  - 自愈线程入口（_start_healing）与手动自愈路由入口
  - HealRound claim（Phase A HealRecord 创建）与 Phase B Finalization
  - 旧逐 step HealService._save_heal_record

注意：ExecutionFinalizer 自身的收敛改写不属于被守卫范围——收敛发生在终态
写入之前，同一事务内完成（Terminal Commit Atomicity）。
"""

from app.exceptions import SealedExecutionError


def guard_not_sealed(db, execution_id: int) -> None:
    """Execution 已终态 → 抛 SealedExecutionError（409）；否则放行。

    Execution 不存在时不抛（由调用方按各自语义处理）。
    """
    from app.models.execution import Execution
    from app.services.execution_finalizer import TERMINAL_STATUSES

    row = db.query(Execution).filter(Execution.id == execution_id).first()
    if row is not None and row.status in TERMINAL_STATUSES:
        raise SealedExecutionError(
            f"执行已封存（status={row.status}），禁止修改执行数据"
        )
