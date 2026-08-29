"""执行管理路由 — 创建异步执行 + 详情 + 状态轮询 + 停止

设计:
  - 路由层只负责参数校验和调用编排器
  - 编排器负责流程控制（生成→执行→监听→报告）
  - 路由层不直接创建 DB 记录（由 PlaywrightService 处理）
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.dependencies import get_db, get_orchestrator
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.test_case import TestCase
from app.models.project import Project
from app.services.execution_state import set_stop_flag
from app.services.orchestrator import TestOrchestrator
from app.exceptions import NotFoundException, ValidationException
from app.schemas import ApiResponse

router = APIRouter(tags=["执行引擎"])


class CreateExecutionBody(BaseModel):
    case_ids: list[int] = Field(..., min_length=1, description="要执行的用例 ID 列表")
    mode: str = Field(default="headless", description="headless / headed")
    batch_name: str | None = Field(default=None, description="批次名称")


# ═══════════════════════════════════════════════
# 创建 + 启动执行（通过编排器）
# ═══════════════════════════════════════════════

@router.post(
    "/projects/{project_id}/executions",
    response_model=ApiResponse,
    summary="创建并启动执行批次",
)
async def create_execution(
    project_id: int,
    body: CreateExecutionBody,
    db: Session = Depends(get_db),
):
    """创建执行批次并异步启动 Playwright 执行

    流程（由编排器控制）:
      1. 检查用例是否已生成代码（未生成则自动生成）
      2. 创建 Execution + ExecutionStep 记录（PlaywrightService）
      3. 后台线程启动 Playwright 执行
      4. 自动监听执行完成 → 生成报告

    立即返回 execution_id，前端通过 GET /executions/{id}/status 轮询进度。
    """
    if body.mode not in ("headless", "headed"):
        raise ValidationException("mode 必须为 headless 或 headed")

    orchestrator = get_orchestrator(db)

    try:
        # 检查是否所有用例都已生成代码
        from app.models.generated_code import GeneratedCode
        missing = []
        for cid in body.case_ids:
            gen = (
                db.query(GeneratedCode)
                .filter(GeneratedCode.case_id == cid, GeneratedCode.is_valid == 1)
                .first()
            )
            if not gen:
                case = db.query(TestCase).filter(
                    TestCase.id == cid,
                    TestCase.project_id == project_id,
                ).first()
                name = case.case_name if case else f"ID={cid}"
                missing.append(name)

        if missing:
            raise ValidationException(
                f"以下用例尚未生成有效代码: {', '.join(missing)}"
            )

        # 获取项目平台类型
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundException(f"项目 {project_id} 不存在")
        platform = getattr(project, "platform", "web")

        result = await orchestrator.run_execute_only(
            project_id=project_id,
            case_ids=body.case_ids,
            mode=body.mode,
            batch_name=body.batch_name,
            platform=platform,
        )
        return ApiResponse(data=result)

    except ValidationException:
        raise
    except Exception as e:
        raise ValidationException(f"启动执行失败: {str(e)}")


# ═══════════════════════════════════════════════
# 项目执行列表
# ═══════════════════════════════════════════════

@router.get(
    "/projects/{project_id}/executions",
    response_model=ApiResponse,
    summary="获取项目下的执行列表",
)
def list_project_executions(project_id: int, db: Session = Depends(get_db)):
    from app.models.project import Project

    project = db.query(Project).filter(Project.id == project_id).first()
    platform = project.platform if project else "web"
    executions = (
        db.query(Execution)
        .filter(Execution.project_id == project_id)
        .order_by(Execution.created_at.desc())
        .all()
    )

    # 批量查询步骤，实时聚合用例级通过/失败统计
    # （自愈过程中 Execution 表的缓存统计不会实时更新，需以步骤状态为准）
    from collections import defaultdict
    exec_ids = [e.id for e in executions]
    case_statuses: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    if exec_ids:
        steps = (
            db.query(ExecutionStep)
            .filter(ExecutionStep.execution_id.in_(exec_ids))
            .all()
        )
        for s in steps:
            case_statuses[s.execution_id][s.case_id].append(s.status)

    items = []
    for e in executions:
        # 实时聚合：用例通过 = 所有步骤 success；用例失败 = 存在 failed 步骤
        cstatus_map = case_statuses.get(e.id, {})
        passed = 0
        failed = 0
        if cstatus_map:
            for sts in cstatus_map.values():
                if "failed" in sts:
                    failed += 1
                elif all(st == "success" for st in sts):
                    passed += 1
        else:
            # 无步骤记录（刚创建等）回退到缓存统计
            passed = e.passed_cases or 0
            failed = e.failed_cases or 0

        total = e.total_cases or 0
        progress = round((passed + failed) / total * 100) if total > 0 else 0

        items.append({
            "id": e.id,
            "batch_name": e.batch_name,
            "platform": platform,
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "status": e.status,
            "execution_mode": e.execution_mode,
            "progress": progress,
            "duration": int((e.end_time - e.start_time).total_seconds()) if e.end_time and e.start_time else None,
            "start_time": str(e.start_time) if e.start_time else None,
            "end_time": str(e.end_time) if e.end_time else None,
            "created_at": str(e.created_at) if e.created_at else None,
        })

    return ApiResponse(data={
        "items": items,
        "total": len(executions),
    })


# ═══════════════════════════════════════════════
# 执行详情
# ═══════════════════════════════════════════════

@router.get(
    "/executions/{execution_id}",
    response_model=ApiResponse,
    summary="获取执行详情（含步骤列表）",
)
def get_execution_detail(execution_id: int, project_id: int = None, db: Session = Depends(get_db)):
    """获取执行批次的完整详情"""
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if project_id is not None and execution.project_id != project_id:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    steps = (
        db.query(ExecutionStep)
        .filter(ExecutionStep.execution_id == execution_id)
        .order_by(ExecutionStep.case_id, ExecutionStep.step_index)
        .all()
    )

    # 按 case_id 聚合成 case_results
    from collections import OrderedDict

    case_groups = OrderedDict()
    case_ids = set()
    for s in steps:
        case_ids.add(s.case_id)
        if s.case_id not in case_groups:
            case_groups[s.case_id] = []
        case_groups[s.case_id].append(s)

    # 批量查询用例名
    case_name_map = {}
    if case_ids:
        cases = db.query(TestCase).filter(TestCase.id.in_(case_ids)).all()
        case_name_map = {c.id: c.case_name for c in cases}

    case_results = []
    for cid, csteps in case_groups.items():
        statuses = {cs.status for cs in csteps}
        if "failed" in statuses:
            case_status = "failed"
        elif "success" in statuses and all(cs.status == "success" for cs in csteps):
            case_status = "success"
        elif "running" in statuses:
            case_status = "running"
        elif "pending" in statuses:
            case_status = "pending"
        elif "skipped" in statuses:
            case_status = "skipped"
        else:
            case_status = "unknown"

        case_results.append({
            "case_id": cid,
            "case_name": case_name_map.get(cid, f"用例 #{cid}"),
            "status": case_status,
            "step_count": len(csteps),
            "duration": sum(cs.duration_ms or 0 for cs in csteps),
            "steps": [
                {
                    "id": cs.id,
                    "step_index": cs.step_index,
                    "action": cs.action,
                    "target_selector": cs.target_selector,
                    "input_value": cs.input_value,
                    "status": cs.status,
                    "screenshot_before": cs.screenshot_before,
                    "screenshot_after": cs.screenshot_after,
                    "log_output": cs.log_output,
                    "error_message": cs.error_message,
                    "exception_type": cs.exception_type,
                    "duration_ms": cs.duration_ms,
                    "created_at": str(cs.created_at) if cs.created_at else None,
                }
                for cs in csteps
            ],
        })

    return ApiResponse(data={
        "id": execution.id,
        "project_id": execution.project_id,
        "batch_name": execution.batch_name,
        "total_cases": execution.total_cases,
        "passed_cases": execution.passed_cases,
        "failed_cases": execution.failed_cases,
        "status": execution.status,
        "start_time": str(execution.start_time) if execution.start_time else None,
        "end_time": str(execution.end_time) if execution.end_time else None,
        "execution_mode": execution.execution_mode,
        "created_at": str(execution.created_at) if execution.created_at else None,
        "case_results": case_results,
        "steps": [
            {
                "id": s.id,
                "execution_id": s.execution_id,
                "case_id": s.case_id,
                "step_index": s.step_index,
                "action": s.action,
                "target_selector": s.target_selector,
                "input_value": s.input_value,
                "status": s.status,
                "screenshot_before": s.screenshot_before,
                "screenshot_after": s.screenshot_after,
                "log_output": s.log_output,
                "error_message": s.error_message,
                "duration_ms": s.duration_ms,
                "created_at": str(s.created_at) if s.created_at else None,
            }
            for s in steps
        ],
    })


# ═══════════════════════════════════════════════
# 执行状态轮询
# ═══════════════════════════════════════════════

@router.get(
    "/executions/{execution_id}/status",
    response_model=ApiResponse,
    summary="轮询执行进度",
)
def get_execution_status(execution_id: int, project_id: int = None, db: Session = Depends(get_db)):
    """实时查询执行进度（前端轮询用）

    返回 running / healing / completed / stopped / failed 状态。
    """
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if project_id is not None and execution.project_id != project_id:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    steps = (
        db.query(ExecutionStep)
        .filter(ExecutionStep.execution_id == execution_id)
        .all()
    )

    total = len(steps)
    done = sum(1 for s in steps if s.status in ("success", "failed", "skipped"))
    pct = round(done / total * 100) if total > 0 else 0

    # 获取当前正在执行的用例名
    current_case = None
    for s in steps:
        if s.status == "running":
            case = db.query(TestCase).filter(TestCase.id == s.case_id).first()
            if case:
                current_case = case.case_name
            break

    # 获取最新截图（headed 模式轮询用）
    latest_screenshot = None
    for s in reversed(steps):
        if s.screenshot_after:
            latest_screenshot = s.screenshot_after
            break
        if s.screenshot_before:
            latest_screenshot = s.screenshot_before
            break

    return ApiResponse(data={
        "execution_id": execution_id,
        "status": execution.status,
        "total_cases": execution.total_cases,
        "passed_cases": execution.passed_cases,
        "failed_cases": execution.failed_cases,
        "total_steps": total,
        "completed_steps": done,
        "progress": f"{done}/{total}",
        "percentage": pct,
        "current_case": current_case,
        "latest_screenshot": latest_screenshot,
    })


# ═══════════════════════════════════════════════
# 停止执行
# ═══════════════════════════════════════════════

@router.post(
    "/executions/{execution_id}/stop",
    response_model=ApiResponse,
    summary="停止正在进行的执行",
)
def stop_execution(execution_id: int, project_id: int = None, db: Session = Depends(get_db)):
    """停止正在运行/自愈中的执行批次"""
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if project_id is not None and execution.project_id != project_id:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if execution.status not in ("queued", "running", "healing"):
        return ApiResponse(message=f"执行已结束（{execution.status}），无需停止", data={
            "status": execution.status,
        })

    # 设置停止标志（共享 execution_state，同时覆盖 Playwright 和 Appium 执行）
    set_stop_flag(execution_id)

    # 更新 DB 状态
    execution.status = "stopped"
    execution.end_time = datetime.utcnow()
    db.commit()

    return ApiResponse(data={"status": "stopped"})
