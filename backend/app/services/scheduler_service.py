"""调度服务 — async 后台循环 + cron 解析 + 到点触发

PROJ-V20-SCHED（F1）。设计（Spec §5 / §6 / §13）：
  - 每 TICK_SECONDS tick 一次（Owner 决策 D-5 = 30s），找出 enabled 且
    next_run_at <= now 的任务；
  - 触发只调 orchestrator 正式入口 run_execute_only —— 唯一执行入口，
    禁止直调执行层（Spec §8 / 不变量：无第二 Execution Contract）；
  - 触发后回填 last_run_at / last_execution_id / next_run_at；
  - 重启恢复：只排未来（不盲目补跑错过窗口）；
  - 异常隔离：单条触发失败不影响其它任务。

cron 解析用 croniter（Owner 决策 D-6，版本固定见 requirements.txt）。
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from croniter import croniter

logger = logging.getLogger("autopilot.scheduler")

TICK_SECONDS = 30   # Owner 决策 D-5
_CRON_FIELDS = 5    # 5 段标准 cron（分 时 日 月 周）


# ═══════════════════════════════════════════════
# cron 校验 / 解析
# ═══════════════════════════════════════════════

def _fields(expr: Any) -> list[str]:
    return expr.strip().split() if isinstance(expr, str) else []


def is_valid_cron(expr: Any) -> bool:
    """5 段标准 cron 合法性（croniter 默认接受 6 段含秒，此处显式要求 5 段）"""
    fields = _fields(expr)
    if len(fields) != _CRON_FIELDS:
        return False
    try:
        return bool(croniter.is_valid(" ".join(fields)))
    except Exception:
        return False


def validate_cron(expr: Any) -> None:
    """非法 cron 抛 ValueError（带明细）；给 Pydantic 校验层使用"""
    fields = _fields(expr)
    if len(fields) != _CRON_FIELDS:
        raise ValueError(
            f"cron 表达式必须为 5 段（分 时 日 月 周），实际 {len(fields)} 段: {expr!r}"
        )
    try:
        ok = croniter.is_valid(" ".join(fields))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"非法 cron 表达式: {expr!r} ({e})") from e
    if not ok:
        raise ValueError(f"非法 cron 表达式: {expr!r}")


def compute_next_run(expr: str, base: datetime) -> datetime:
    """下一次触发时刻（严格晚于 base）"""
    return croniter(" ".join(_fields(expr)), base).get_next(datetime)


# ═══════════════════════════════════════════════
# 调度服务
# ═══════════════════════════════════════════════

class SchedulerService:
    """定时任务调度器（单 worker 前提；不承担 HA）

    参数可注入以便测试：
      - db：显式会话（测试 / 请求内手动触发）；None 时每次操作自建会话；
      - orchestrator：执行编排器；None 时按需经依赖工厂自建；
      - tick_seconds：tick 间隔（默认 D-5 = 30s）。
    """

    def __init__(
        self,
        db: Any = None,
        orchestrator: Any = None,
        tick_seconds: int = TICK_SECONDS,
        session_factory: Optional[Callable] = None,
    ) -> None:
        self._db = db
        self._orchestrator = orchestrator
        self.tick_seconds = tick_seconds
        self._session_factory = session_factory
        self._task: Optional[asyncio.Task] = None

    # ── 会话 / 编排器获取 ──

    def _open_db(self) -> tuple[Any, bool]:
        if self._db is not None:
            return self._db, False
        if self._session_factory is not None:
            return self._session_factory(), True
        from app.db.database import SessionLocal
        return SessionLocal(), True

    def _get_orchestrator(self, db: Any) -> Any:
        if self._orchestrator is not None:
            return self._orchestrator
        from app.dependencies import get_orchestrator
        return get_orchestrator(db)

    # ── 单次 tick：找出到点任务并触发 ──

    async def run_due(self, now: Optional[datetime] = None) -> list[dict]:
        """跑一轮：enabled 且 next_run_at <= now 的任务逐个触发（异常隔离）"""
        now = now or datetime.utcnow()
        from app.models.schedule import Schedule

        db, owns = self._open_db()
        try:
            due = (
                db.query(Schedule)
                .filter(
                    Schedule.enabled.is_(True),
                    Schedule.next_run_at.isnot(None),
                    Schedule.next_run_at <= now,
                )
                .order_by(Schedule.next_run_at.asc())
                .all()
            )
            results: list[dict] = []
            for sched in due:
                try:
                    results.append(await self._trigger(db, sched, now, advance_next=True))
                except Exception as e:  # noqa: BLE001 — 单条失败不影响其它
                    logger.warning("调度触发失败 schedule_id=%s: %s", sched.id, str(e)[:200])
                    results.append({
                        "schedule_id": sched.id,
                        "execution_id": None,
                        "status": "failed",
                        "error": str(e),
                    })
            return results
        finally:
            if owns:
                db.close()

    async def trigger_now(self, schedule_id: int, now: Optional[datetime] = None) -> dict:
        """手动触发一次（与定时触发同一入口；不改动调度节奏）"""
        now = now or datetime.utcnow()
        from app.models.schedule import Schedule
        from app.exceptions import NotFoundException

        db, owns = self._open_db()
        try:
            sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
            if sched is None:
                raise NotFoundException(f"定时任务 {schedule_id} 不存在")
            return await self._trigger(db, sched, now, advance_next=False)
        finally:
            if owns:
                db.close()

    async def _trigger(self, db: Any, schedule: Any, now: datetime, *,
                       advance_next: bool) -> dict:
        """触发一次：走 orchestrator 正式入口 → 回填调度字段"""
        from app.models.project import Project

        try:
            cfg = json.loads(schedule.exec_config_json) if schedule.exec_config_json else {}
        except (TypeError, ValueError):
            cfg = {}
        case_ids = [int(c) for c in (cfg.get("case_ids") or [])]
        mode = cfg.get("mode") or "headless"

        project = db.query(Project).filter(Project.id == schedule.project_id).first()
        platform = getattr(project, "platform", "web") if project is not None else "web"

        orchestrator = self._get_orchestrator(db)
        result = await orchestrator.run_execute_only(
            project_id=schedule.project_id,
            case_ids=case_ids,
            mode=mode,
            batch_name=schedule.name,
            platform=platform,
        )
        execution_id = result.get("execution_id") if isinstance(result, dict) else None

        schedule.last_run_at = now
        schedule.last_execution_id = execution_id
        if advance_next:
            schedule.next_run_at = compute_next_run(schedule.cron_expr, now)
        db.commit()
        return {
            "schedule_id": schedule.id,
            "execution_id": execution_id,
            "status": "triggered",
        }

    # ── 重启恢复：只排未来 ──

    def recompute_next_runs(self, now: Optional[datetime] = None) -> int:
        """对 enabled 且 next_run_at 为空/已过的任务，重排到未来（不补跑错过窗口）"""
        now = now or datetime.utcnow()
        from app.models.schedule import Schedule

        db, owns = self._open_db()
        try:
            rows = db.query(Schedule).filter(Schedule.enabled.is_(True)).all()
            changed = 0
            for s in rows:
                if not is_valid_cron(s.cron_expr or ""):
                    continue
                if s.next_run_at is None or s.next_run_at <= now:
                    try:
                        s.next_run_at = compute_next_run(s.cron_expr, now)
                    except Exception:  # noqa: BLE001
                        continue
                    changed += 1
            if changed:
                db.commit()
            return changed
        finally:
            if owns:
                db.close()

    # ── 后台循环（lifespan 启动）──

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — tick 异常不致循环退出
                logger.warning("调度 tick 异常（继续）: %s", str(e)[:200])
            await asyncio.sleep(self.tick_seconds)

    async def start(self) -> None:
        """启动后台循环（幂等；先按 next_run_at 重排未来）"""
        try:
            self.recompute_next_runs()
        except Exception as e:  # noqa: BLE001
            logger.warning("调度 next_run_at 重算跳过: %s", str(e)[:200])
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass


# 进程内单例（lifespan 启动 / 停止）
scheduler_service = SchedulerService()
