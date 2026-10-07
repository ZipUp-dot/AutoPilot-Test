"""定时任务管理路由 — CRUD + enable/disable + 手动触发

Spec §6 / §8：
  - 手动触发与定时触发同权，都走 orchestrator 正式入口（唯一执行入口）；
  - disable（停止调度）只改 schedules 实体（enabled=False + stop_requested_at），
    不触碰运行中 Execution —— Execution 的停止走 POST /executions/{id}/stop。
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_orchestrator
from app.exceptions import NotFoundException
from app.models.project import Project
from app.models.schedule import (
    Schedule,
    ScheduleCreate,
    ScheduleUpdate,
    ScheduleResponse,
)
from app.schemas import ApiResponse
from app.services.scheduler_service import SchedulerService, compute_next_run

router = APIRouter(tags=["定时执行"])


def _to_out(sched: Schedule) -> dict:
    return ScheduleResponse.model_validate(sched).model_dump()


def _ensure_project(db: Session, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise NotFoundException(f"项目 {project_id} 不存在")
    return project


def _get_schedule(db: Session, schedule_id: int) -> Schedule:
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if sched is None:
        raise NotFoundException(f"定时任务 {schedule_id} 不存在")
    return sched


# ═══════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════

@router.post(
    "/projects/{project_id}/schedules",
    response_model=ApiResponse,
    summary="创建定时任务",
)
def create_schedule(project_id: int, body: ScheduleCreate, db: Session = Depends(get_db)):
    _ensure_project(db, project_id)
    now = datetime.utcnow()
    sched = Schedule(
        project_id=project_id,
        name=body.name,
        cron_expr=body.cron_expr.strip(),
        exec_config_json=json.dumps(body.exec_config_json, ensure_ascii=False),
        enabled=True,
        next_run_at=compute_next_run(body.cron_expr, now),
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return ApiResponse(data=_to_out(sched))


@router.get(
    "/projects/{project_id}/schedules",
    response_model=ApiResponse,
    summary="项目下的定时任务列表",
)
def list_schedules(project_id: int, db: Session = Depends(get_db)):
    _ensure_project(db, project_id)
    rows = (
        db.query(Schedule)
        .filter(Schedule.project_id == project_id)
        .order_by(Schedule.id.desc())
        .all()
    )
    return ApiResponse(data={"items": [_to_out(r) for r in rows], "total": len(rows)})


@router.get("/schedules/{schedule_id}", response_model=ApiResponse, summary="定时任务详情")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    return ApiResponse(data=_to_out(_get_schedule(db, schedule_id)))


@router.put("/schedules/{schedule_id}", response_model=ApiResponse, summary="更新定时任务")
def update_schedule(schedule_id: int, body: ScheduleUpdate, db: Session = Depends(get_db)):
    sched = _get_schedule(db, schedule_id)
    now = datetime.utcnow()

    if body.name is not None:
        sched.name = body.name
    if body.cron_expr is not None:
        sched.cron_expr = body.cron_expr.strip()
        if sched.enabled:
            sched.next_run_at = compute_next_run(body.cron_expr, now)
    if body.exec_config_json is not None:
        sched.exec_config_json = json.dumps(body.exec_config_json, ensure_ascii=False)
    if body.enabled is not None and body.enabled != sched.enabled:
        sched.enabled = body.enabled
        if body.enabled:
            sched.next_run_at = compute_next_run(sched.cron_expr, now)

    db.commit()
    db.refresh(sched)
    return ApiResponse(data=_to_out(sched))


@router.delete("/schedules/{schedule_id}", response_model=ApiResponse, summary="删除定时任务")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    sched = _get_schedule(db, schedule_id)
    db.delete(sched)
    db.commit()
    return ApiResponse(data={"id": schedule_id, "deleted": True})


# ═══════════════════════════════════════════════
# 启停（Schedule Stop ≠ Execution Stop）
# ═══════════════════════════════════════════════

@router.post("/schedules/{schedule_id}/enable", response_model=ApiResponse, summary="启用定时任务")
def enable_schedule(schedule_id: int, db: Session = Depends(get_db)):
    sched = _get_schedule(db, schedule_id)
    now = datetime.utcnow()
    sched.enabled = True
    if sched.next_run_at is None or sched.next_run_at <= now:
        sched.next_run_at = compute_next_run(sched.cron_expr, now)
    db.commit()
    db.refresh(sched)
    return ApiResponse(data=_to_out(sched))


@router.post(
    "/schedules/{schedule_id}/disable",
    response_model=ApiResponse,
    summary="停止调度（不影响运行中实例）",
)
def disable_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """停止调度：enabled=False + 写 stop_requested_at（Schedule Stop 唯一权威）。

    只改 schedules 实体；运行中的 Execution 不受影响（其停止走 Execution Stop）。
    """
    sched = _get_schedule(db, schedule_id)
    sched.enabled = False
    sched.stop_requested_at = datetime.utcnow()
    db.commit()
    db.refresh(sched)
    return ApiResponse(data=_to_out(sched))


# ═══════════════════════════════════════════════
# 手动触发一次（与定时触发同权）
# ═══════════════════════════════════════════════

@router.post(
    "/schedules/{schedule_id}/trigger",
    response_model=ApiResponse,
    summary="手动触发一次（走 Admission）",
)
async def trigger_schedule(schedule_id: int, db: Session = Depends(get_db)):
    _get_schedule(db, schedule_id)  # 存在性校验（404 语义）
    orchestrator = get_orchestrator(db)
    svc = SchedulerService(db=db, orchestrator=orchestrator)
    result = await svc.trigger_now(schedule_id)
    return ApiResponse(data=result)
