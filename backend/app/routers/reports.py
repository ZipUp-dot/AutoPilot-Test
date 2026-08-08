"""报告路由 — 生成报告 + 查询报告 + 静态文件访问

接口:
  1. POST /api/v1/executions/{id}/reports/generate — 生成报告
  2. GET  /api/v1/executions/{id}/reports          — 获取报告信息
  3. GET  /reports/{filename}                       — 直接访问报告 HTML（挂载静态文件）
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from pathlib import Path

from app.dependencies import get_db
from app.config import settings
from app.schemas import ApiResponse
from app.services.report_service import ReportService
from app.exceptions import NotFoundException

logger = logging.getLogger("autopilot.report")

router = APIRouter(tags=["测试报告"])


# ═══════════════════════════════════════════════
# POST — 生成报告
# ═══════════════════════════════════════════════

@router.post(
    "/executions/{execution_id}/reports/generate",
    response_model=ApiResponse,
    summary="生成 HTML 测试报告",
)
def generate_report(execution_id: int, db: Session = Depends(get_db)):
    """聚合执行数据，渲染 Jinja2 模板，生成完全离线可查看的 HTML 报告。

    返回:
        { "code": 0, "data": { "report_id": 1, "download_url": "/reports/execution_1_report.html" } }
    """
    service = ReportService(db)
    try:
        result = service.generate(execution_id)
        return ApiResponse(data=result)
    except ValueError as e:
        raise NotFoundException(str(e))
    except Exception as e:
        logger.exception("报告生成失败: execution_id=%s", execution_id)
        raise HTTPException(status_code=500, detail=f"报告生成失败: {str(e)}")


# ═══════════════════════════════════════════════
# GET — 获取报告信息
# ═══════════════════════════════════════════════

@router.get(
    "/executions/{execution_id}/reports",
    response_model=ApiResponse,
    summary="获取报告信息",
)
def get_report(execution_id: int, db: Session = Depends(get_db)):
    """获取已生成的报告信息（不重新生成）。

    返回:
        { "code": 0, "data": { "report_id": 1, "summary": {...}, "download_url": "..." } }
    """
    service = ReportService(db)
    info = service.get_report_info(execution_id)
    if not info:
        raise NotFoundException(f"执行批次 {execution_id} 的报告尚未生成")
    return ApiResponse(data=info)
