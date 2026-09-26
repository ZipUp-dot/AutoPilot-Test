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
from app.services.execution_finalizer import ExecutionFinalizer
from app.services.orchestrator import TestOrchestrator
from app.exceptions import NotFoundException, ValidationException
from app.schemas import ApiResponse
from app.utils import terminal_reason as _tr

router = APIRouter(tags=["执行引擎"])

# Execution.status → Case 收敛 context（Resolved 兜底 reason；Case 自身证据优先）
_EXEC_STATUS_TO_REASON = {
    "completed": _tr.NORMAL_SUCCESS,
    "stopped": _tr.USER_STOPPED,
    "failed": _tr.EXECUTION_FAILED,
    "interrupted": _tr.INTERRUPTED,
}


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
        # 代码来源/存在性/effective 校验统一由 ExecutionAdmissionService 完成；
        # 本路由禁止再手工查询 GeneratedCode（latest 后门）。
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

    # 批量查询步骤，实时聚合用例级通过/失败统计（唯一真源 = CaseStateResolver）
    from collections import defaultdict
    from app.utils.case_state_resolver import resolve as _resolve
    exec_ids = [e.id for e in executions]
    case_steps: dict[int, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    if exec_ids:
        steps = (
            db.query(ExecutionStep)
            .filter(ExecutionStep.execution_id.in_(exec_ids))
            .all()
        )
        for s in steps:
            case_steps[s.execution_id][s.case_id].append({
                "status": s.status,
                "error_type": s.error_type,
                "skip_reason": s.skip_reason,
                "exception_type": s.exception_type,
            })

    items = []
    for e in executions:
        # 实时聚合：每个用例按 steps 真值表解析（Case 自身证据优先，Execution.status 仅兜底）
        csteps_map = case_steps.get(e.id, {})
        passed = 0
        failed = 0
        if csteps_map:
            context = {"reason": _EXEC_STATUS_TO_REASON.get(e.status)}
            for csteps in csteps_map.values():
                status, _ = _resolve(csteps, context)
                if status == "success":
                    passed += 1
                elif status == "failed":
                    failed += 1
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
# 公共聚合（详情/状态共用，保证统计与用例列表同一数据源）
# ═══════════════════════════════════════════════

def _build_case_results(db: Session, steps: list[ExecutionStep],
                        execution_status: str = "") -> list[dict]:
    """按 case_id 把步骤聚合成用例级结果（唯一真源 = CaseStateResolver）。

    status 由 steps 真值表推出；terminal_reason 由 status + execution 收敛
    context + step 层 error_type/skip_reason 推出（禁止用 Execution.status 反推）。
    """
    from collections import OrderedDict
    from app.utils.case_state_resolver import resolve, step_to_dict

    case_groups: "OrderedDict[int, list[ExecutionStep]]" = OrderedDict()
    case_ids = set()
    for s in steps:
        case_ids.add(s.case_id)
        case_groups.setdefault(s.case_id, []).append(s)

    case_name_map = {}
    if case_ids:
        cases = db.query(TestCase).filter(TestCase.id.in_(case_ids)).all()
        case_name_map = {c.id: c.case_name for c in cases}

    context = {"reason": _EXEC_STATUS_TO_REASON.get(execution_status)}

    case_results = []
    for cid, csteps in case_groups.items():
        status, terminal_reason = resolve(
            [step_to_dict(cs) for cs in csteps], context
        )
        case_results.append({
            "case_id": cid,
            "case_name": case_name_map.get(cid, f"用例 #{cid}"),
            "status": status,
            "terminal_reason": terminal_reason,
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
    return case_results


def _compute_case_stats(case_results: list[dict]) -> dict:
    """基于用例级结果聚合通过/失败/跳过/总耗时（口径与用例列表一致）"""
    passed = sum(1 for c in case_results if c["status"] == "success")
    failed = sum(1 for c in case_results if c["status"] == "failed")
    skipped = sum(1 for c in case_results if c["status"] == "skipped")
    total_duration = sum(c["duration"] for c in case_results)
    return {
        "passed_cases": passed,
        "failed_cases": failed,
        "skipped": skipped,
        "total_cases": passed + failed + skipped,
        "total_duration": total_duration,
    }


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

    case_results = _build_case_results(db, steps, execution.status)
    stats = _compute_case_stats(case_results)

    return ApiResponse(data={
        "id": execution.id,
        "project_id": execution.project_id,
        "batch_name": execution.batch_name,
        "total_cases": stats["total_cases"],
        "passed_cases": stats["passed_cases"],
        "failed_cases": stats["failed_cases"],
        "skipped": stats["skipped"],
        "total_duration": stats["total_duration"],
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

    # 与详情接口同一数据源，返回实时聚合的用例结果与统计，
    # 使前端轮询时统计卡片与用例列表保持同一口径。
    case_results = _build_case_results(db, steps, execution.status)
    stats = _compute_case_stats(case_results)

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
        "total_cases": stats["total_cases"],
        "passed_cases": stats["passed_cases"],
        "failed_cases": stats["failed_cases"],
        "skipped": stats["skipped"],
        "total_duration": stats["total_duration"],
        "case_results": case_results,
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
    """停止执行批次（P0-9 Stop 语义）

    状态转换（钉死，禁止其他转换）：
      - queued → stopped：ExecutionFinalizer.seal_stopped 六步收口事务
        （stop_requested_at + stopped + 全部 Case/Step→skipped(user_stopped) +
        open HealRound→cancelled_by_recovery + runtime_state 终态）。Stop API
        只是触发器，禁止自行写 status=stopped 后再补 Seal。
      - running / healing → 保持原 status 不动，仅写 stop_requested_at + 内存 flag，
        返回 {stop_requested: true}；执行器 case loop 结束后由同一 Finalizer 走
        stopped 收口。
      - 已终态（completed/stopped/failed/interrupted）→ 幂等返回，不重复改写。
    """
    from app.services.execution_state import set_stop_flag, get_execution_lock

    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if project_id is not None and execution.project_id != project_id:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    if execution.status in ("completed", "failed", "interrupted"):
        return ApiResponse(message=f"执行已结束（{execution.status}），无需停止", data={
            "status": execution.status,
        })
    if execution.status == "stopped":
        return ApiResponse(message="执行已停止", data={
            "status": "stopped", "stop_requested": True,
        })
    if execution.status == "queued":
        # 六步收口事务（唯一 stopped 出口；Stop API 只是触发它）
        ExecutionFinalizer(db).seal_stopped(execution_id)
        set_stop_flag(execution_id)
        return ApiResponse(data={"status": "stopped", "stop_requested": True})

    # running / healing：只写 stop_requested_at（线性化边界内重读最新状态，防并发改写）
    with get_execution_lock(execution_id):
        execution = (
            db.query(Execution)
            .filter(Execution.id == execution_id)
            .with_for_update()
            .first()
        )
        if execution is None:
            raise NotFoundException(f"执行批次 {execution_id} 不存在")
        # 线性化后重读：可能已被并发 Stop / Finalizer 抢先改写
        if execution.status in ("completed", "failed", "interrupted"):
            return ApiResponse(message=f"执行已结束（{execution.status}），无需停止", data={
                "status": execution.status,
            })
        if execution.status == "stopped":
            return ApiResponse(message="执行已停止", data={
                "status": "stopped", "stop_requested": True,
            })
        # 重复 Stop 幂等：stop_requested_at 已写入则不再改写
        if execution.stop_requested_at is None:
            execution.stop_requested_at = datetime.utcnow()
        db.commit()

    set_stop_flag(execution_id)
    return ApiResponse(data={"status": execution.status, "stop_requested": True})
