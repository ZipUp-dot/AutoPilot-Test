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
from app.services.playwright_service import set_stop_flag
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
                case = db.query(TestCase).filter(TestCase.id == cid).first()
                name = case.case_name if case else f"ID={cid}"
                missing.append(name)

        if missing:
            raise ValidationException(
                f"以下用例尚未生成有效代码: {', '.join(missing)}"
            )

        result = await orchestrator.run_execute_only(
            project_id=project_id,
            case_ids=body.case_ids,
            mode=body.mode,
            batch_name=body.batch_name,
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
    executions = (
        db.query(Execution)
        .filter(Execution.project_id == project_id)
        .order_by(Execution.created_at.desc())
        .all()
    )
    return ApiResponse(data={
        "items": [
            {
                "id": e.id,
                "batch_name": e.batch_name,
                "total_cases": e.total_cases,
                "passed_cases": e.passed_cases,
                "failed_cases": e.failed_cases,
                "status": e.status,
                "execution_mode": e.execution_mode,
                "start_time": str(e.start_time) if e.start_time else None,
                "end_time": str(e.end_time) if e.end_time else None,
                "created_at": str(e.created_at) if e.created_at else None,
            }
            for e in executions
        ],
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
def get_execution_detail(execution_id: int, db: Session = Depends(get_db)):
    """获取执行批次的完整详情"""
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    steps = (
        db.query(ExecutionStep)
        .filter(ExecutionStep.execution_id == execution_id)
        .order_by(ExecutionStep.case_id, ExecutionStep.step_index)
        .all()
    )

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
def get_execution_status(execution_id: int, db: Session = Depends(get_db)):
    """实时查询执行进度（前端轮询用）

    返回 running / healing / completed / stopped / failed 状态。
    """
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
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
def stop_execution(execution_id: int, db: Session = Depends(get_db)):
    """停止正在运行/自愈中的执行批次"""
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if execution.status not in ("running", "healing"):
        return ApiResponse(message=f"执行已结束（{execution.status}），无需停止", data={
            "status": execution.status,
        })

    # 设置停止标志
    set_stop_flag(execution_id)

    # 更新 DB 状态
    execution.status = "stopped"
    execution.end_time = datetime.utcnow()
    db.commit()

    return ApiResponse(data={"status": "stopped"})
