"""指标路由 — 只读 KPI 聚合

接口:
  GET /api/v1/metrics?project_id=<可选> — 四项 KPI（只读聚合，禁止写）
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas import ApiResponse
from app.services.metrics_service import MetricsService

router = APIRouter(tags=["指标"])


@router.get(
    "/metrics",
    response_model=ApiResponse,
    summary="KPI 聚合指标（70% 首生成有效率 / 85% 最终成功率 / Coverage）",
)
def get_metrics(
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """只读聚合指标。

    返回:
        { "code": 0, "data": {
            "first_generation_success_rate": {"rate":..., "numerator":..., "denominator":...},
            "final_success_rate": {...},
            "pipeline_coverage": {...},
            "execution_start_coverage": {...},
        } }
    """
    return ApiResponse(data=MetricsService(db).overview(project_id))
