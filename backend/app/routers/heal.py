"""自愈路由 — 手动触发自愈修复 + 自愈记录查询

手动触发（调试用，同步等待结果）:
  POST /executions/{execution_id}/heal
  Body: { "case_id": 1, "step_index": 3 }
  Response: { "code": 0, "data": { "heal_id": 1, "healed_code": "...", "retry_status": "success", "retry_count": 2 } }

记录查询:
  GET /executions/{execution_id}/heal-records
  Response: { "code": 0, "data": { "items": [...], "total": 5 } }
"""

import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.config import settings
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.project import Project
from app.models.heal_record import HealRecord
from app.models.test_case import TestCase
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
    """对指定失败的用例启动 Case 级 HealRound，同步等待完成后返回结果。

    返回:
        { "code": 0, "data": { "heal_id": 1, "healed_code": "...", "retry_status": "success", "error_type": null } }
    """
    from app.services.execution_finalizer import TERMINAL_STATUSES

    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise NotFoundException(f"执行批次 {execution_id} 不存在")

    # 验收 6：四终态（completed/stopped/failed/interrupted）一律拒绝（Seal 规则）
    if execution.status in TERMINAL_STATUSES:
        raise ValidationException(
            f"执行已处于终态 {execution.status}，禁止手动自愈"
        )

    # 定位 root failed step（step_index 最小者）；其余 failed step 作上下文
    step = (
        db.query(ExecutionStep)
        .filter(
            ExecutionStep.execution_id == execution_id,
            ExecutionStep.case_id == body.case_id,
            ExecutionStep.status == "failed",
        )
        .order_by(ExecutionStep.step_index)
        .first()
    )
    if not step:
        # 兼容旧契约：按 step_index 定位以区分「不存在」与「非失败」
        any_step = (
            db.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == execution_id,
                ExecutionStep.case_id == body.case_id,
                ExecutionStep.step_index == body.step_index,
            )
            .first()
        )
        if not any_step:
            raise NotFoundException(
                f"步骤 case={body.case_id} step={body.step_index} 不存在"
            )
        raise ValidationException(
            f"步骤状态为 {any_step.status}，非失败状态无需自愈"
        )

    project_id = execution.project_id
    project = db.query(Project).filter(Project.id == project_id).first()
    platform = project.platform if project else "web"
    if platform != "web":
        raise ValidationException(
            f"Android 项目手动自愈未启用（platform={platform}），请经执行引擎自愈线程触发"
        )

    # P1-1：手动 Heal 属 Admission 后上下文，URL/launch 只读 Manifest 冻结快照，
    # 禁止回读 Project 当前 target_url / browser_type（platform 校验已在上方完成）
    from app.services.playwright_service import _load_manifest, _env_from_manifest, _resolve_launcher
    from app.utils.url_builder import build_target_url
    env = _env_from_manifest(_load_manifest(execution))
    heal_target_url = build_target_url(env["target_url"], env["test_path"])
    heal_browser_type = env["browser_type"]
    heal_headless = env["headless"]

    # 更新执行状态为 healing
    if execution.status not in ("healing", "completed", "stopped"):
        execution.status = "healing"
        db.commit()

    # 同步执行自愈（统一走 HealRoundService；同 execution+case 已有 HealRecord 由 claim 拒绝）
    from app.services.heal_service import HealRoundService

    heal_service = HealRoundService(db)

    async def _heal():
        from playwright.async_api import async_playwright
        from app.utils.url_policy import UrlPolicy, install_network_policy

        async with async_playwright() as pw:
            browser = await _resolve_launcher(pw, heal_browser_type).launch(headless=heal_headless)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                service_workers="block",
            )
            await install_network_policy(
                context,
                UrlPolicy(
                    heal_target_url,
                    allowed_hosts=env["allowed_hosts"],
                    allowed_ports=env["allowed_ports"],
                ),
            )
            page = await context.new_page()
            page.set_default_timeout(settings.PLAYWRIGHT_TIMEOUT)

            try:
                await page.goto(heal_target_url, wait_until="networkidle")
            except Exception as e:
                logger.warning("手动自愈导航失败: %s", e)

            result = await heal_service.heal_case(
                execution_id=execution_id,
                case_id=body.case_id,
                project_id=project_id,
                page=page,
                platform="web",
                manual=True,
            )

            await context.close()
            await browser.close()
            return result

    result = await _heal()

    return ApiResponse(data={
        "heal_id": result.heal_id,
        "healed_code": result.healed_code,
        "retry_status": result.retry_status,
        "error_type": result.error_type,
        "error_message": result.error_message,
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
    step_ids = select(ExecutionStep.id).where(
        ExecutionStep.execution_id == execution_id
    )
    records = (
        db.query(HealRecord)
        .filter(HealRecord.execution_step_id.in_(step_ids))
        .order_by(HealRecord.created_at.desc())
        .all()
    )

    # 批量查询 step 和 case 信息
    step_ids_list = [r.execution_step_id for r in records]
    step_map = {}
    if step_ids_list:
        steps = (
            db.query(ExecutionStep)
            .filter(ExecutionStep.id.in_(step_ids_list))
            .all()
        )
        case_ids = list({s.case_id for s in steps})
        cases = (
            db.query(TestCase)
            .filter(TestCase.id.in_(case_ids))
            .all()
        )
        case_map = {c.id: c.case_name for c in cases}
        for s in steps:
            step_map[s.id] = {"case_name": case_map.get(s.case_id, ""), "step_index": s.step_index}

    items = []
    for r in records:
        step_info = step_map.get(r.execution_step_id, {})
        items.append({
            "id": r.id,
            "execution_step_id": r.execution_step_id,
            "case_name": step_info.get("case_name", ""),
            "step_index": step_info.get("step_index"),
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
