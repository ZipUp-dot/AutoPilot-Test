"""自愈路由 — 手动触发自愈修复 + 自愈记录查询

手动触发（调试用，同步等待结果）:
  POST /executions/{execution_id}/heal
  Body: { "case_id": 1, "step_index": 3 }
  Response: { "code": 0, "data": { "heal_id": 1, "healed_code": "...", "retry_status": "success", "retry_count": 2 } }

记录查询:
  GET /executions/{execution_id}/heal-records
  Response: { "code": 0, "data": { "items": [...], "total": 5 } }
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.config import settings
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.project import Project
from app.models.heal_record import HealRecord
from app.schemas import ApiResponse, HealRequest
from app.exceptions import NotFoundException, ValidationException

logger = logging.getLogger("autopilot.heal")

router = APIRouter(tags=["自愈修复"])


# ═══════════════════════════════════════════════
# 手动触发自愈（同步，调试用）
# ═══════════════════════════════════════════════

@router.post(
    "/executions/{execution_id}/heal",
    response_model=ApiResponse,
    summary="手动触发自愈修复（调试用，同步等待结果）",
)
async def trigger_heal(
    execution_id: int,
    body: HealRequest,
    db: Session = Depends(get_db),
):
    """对指定失败的步骤启动自愈，同步等待完成后返回结果。

    返回:
        { "code": 0, "data": { "heal_id": 1, "healed_code": "...", "retry_status": "success", "retry_count": 2 } }
    """
    # 定位失败步骤
    step = (
        db.query(ExecutionStep)
        .filter(
            ExecutionStep.execution_id == execution_id,
            ExecutionStep.case_id == body.case_id,
            ExecutionStep.step_index == body.step_index,
        )
        .first()
    )
    if not step:
        raise NotFoundException(
            f"步骤 case={body.case_id} step={body.step_index} 不存在"
        )

    if step.status != "failed":
        raise ValidationException(
            f"步骤状态为 {step.status}，非失败状态无需自愈"
        )

    # 获取项目信息
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    project_id = execution.project_id
    project = db.query(Project).filter(Project.id == project_id).first()
    target_url = project.target_url if project else "https://example.com"

    # 更新执行状态为 healing
    if execution.status not in ("healing", "completed", "stopped"):
        execution.status = "healing"
        db.commit()

    # 同步执行自愈
    from app.services.heal_service import HealService

    heal_service = HealService(db)

    async def _heal():
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            page.set_default_timeout(settings.PLAYWRIGHT_TIMEOUT)

            try:
                await page.goto(target_url, wait_until="networkidle")
            except Exception as e:
                logger.warning("手动自愈导航失败: %s", e)

            # 重新查询 step
            step_obj = (
                db.query(ExecutionStep)
                .filter(ExecutionStep.id == step.id)
                .first()
            )
            if not step_obj:
                return None

            result = await heal_service.try_heal_manual(
                execution_id=execution_id,
                step=step_obj,
                page=page,
                project_id=project_id,
                max_retries=3,
            )

            await context.close()
            await browser.close()
            return result

    result = await _heal()

    if result is None:
        raise NotFoundException("步骤已被删除")

    return ApiResponse(data={
        "heal_id": result.heal_id,
        "healed_code": result.healed_code,
        "retry_status": result.retry_status,
        "retry_count": result.retry_count,
    })


# ═══════════════════════════════════════════════
# 自愈记录查询
# ═══════════════════════════════════════════════

@router.get(
    "/executions/{execution_id}/heal-records",
    response_model=ApiResponse,
    summary="查询执行批次的自愈记录",
)
def get_heal_records(execution_id: int, db: Session = Depends(get_db)):
    """获取执行批次的全部自愈记录，按创建时间倒序。

    返回:
        { "code": 0, "data": { "items": [...], "total": 5 } }
    """
    step_ids = (
        db.query(ExecutionStep.id)
        .filter(ExecutionStep.execution_id == execution_id)
        .subquery()
    )
    records = (
        db.query(HealRecord)
        .filter(HealRecord.execution_step_id.in_(step_ids))
        .order_by(HealRecord.created_at.desc())
        .all()
    )

    items = []
    for r in records:
        items.append({
            "id": r.id,
            "execution_step_id": r.execution_step_id,
            "original_code": r.original_code,
            "error_context": json.loads(r.error_context) if r.error_context else None,
            "healed_code": r.healed_code,
            "heal_prompt": r.heal_prompt,
            "retry_status": r.retry_status,
            "retry_count": r.retry_count,
            "created_at": str(r.created_at) if r.created_at else "",
        })

    return ApiResponse(data={
        "items": items,
        "total": len(items),
    })
