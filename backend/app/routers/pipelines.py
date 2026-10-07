"""流水线管理路由 — CRUD + 外部触发（webhook）+ 运行视图

PROJ-V20-CICD（F2）。Spec §6 / §8：
  - 触发只构造执行请求：execution 由 ExecutionAdmission 创建（C-22 教训）；
  - webhook 触发校验 `X-Pipeline-Token`（常量时间比较），非法/缺失 → 401 且零 run；
  - 运行视图与 executions 接口同口径（CaseStateResolver）。
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_orchestrator
from app.exceptions import AppException, NotFoundException
from app.models.pipeline import (
    Pipeline,
    PipelineCreate,
    PipelineResponse,
    PipelineRun,
    PipelineUpdate,
)
from app.models.project import Project
from app.schemas import ApiResponse
from app.services.pipeline_service import PipelineService

router = APIRouter(tags=["CI/CD 流水线"])


def _to_out(pipeline: Pipeline) -> dict:
    return PipelineResponse.model_validate(pipeline).model_dump()


def _ensure_project(db: Session, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise NotFoundException(f"项目 {project_id} 不存在")
    return project


def _get_pipeline(db: Session, pipeline_id: int) -> Pipeline:
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if pipeline is None:
        raise NotFoundException(f"流水线 {pipeline_id} 不存在")
    return pipeline


class TriggerBody(BaseModel):
    trigger_type: str = Field(default="manual", pattern="^(manual|webhook|schedule)$")
    trigger_detail: Optional[dict] = None


# ═══════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════

@router.post(
    "/projects/{project_id}/pipelines",
    response_model=ApiResponse,
    summary="创建流水线",
)
def create_pipeline(project_id: int, body: PipelineCreate, db: Session = Depends(get_db)):
    _ensure_project(db, project_id)
    pipeline = Pipeline(
        project_id=project_id,
        name=body.name,
        trigger_config_json=json.dumps(body.trigger_config_json, ensure_ascii=False),
        stages_json=json.dumps(body.stages_json, ensure_ascii=False),
        enabled=body.enabled,
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return ApiResponse(data=_to_out(pipeline))


@router.get(
    "/projects/{project_id}/pipelines",
    response_model=ApiResponse,
    summary="项目下的流水线列表",
)
def list_pipelines(project_id: int, db: Session = Depends(get_db)):
    _ensure_project(db, project_id)
    rows = (
        db.query(Pipeline)
        .filter(Pipeline.project_id == project_id)
        .order_by(Pipeline.id.desc())
        .all()
    )
    return ApiResponse(data={"items": [_to_out(r) for r in rows], "total": len(rows)})


@router.get("/pipelines/{pipeline_id}", response_model=ApiResponse, summary="流水线详情")
def get_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    return ApiResponse(data=_to_out(_get_pipeline(db, pipeline_id)))


@router.put("/pipelines/{pipeline_id}", response_model=ApiResponse, summary="更新流水线")
def update_pipeline(pipeline_id: int, body: PipelineUpdate, db: Session = Depends(get_db)):
    pipeline = _get_pipeline(db, pipeline_id)
    if body.name is not None:
        pipeline.name = body.name
    if body.trigger_config_json is not None:
        pipeline.trigger_config_json = json.dumps(body.trigger_config_json, ensure_ascii=False)
    if body.stages_json is not None:
        pipeline.stages_json = json.dumps(body.stages_json, ensure_ascii=False)
    if body.enabled is not None:
        pipeline.enabled = body.enabled
    db.commit()
    db.refresh(pipeline)
    return ApiResponse(data=_to_out(pipeline))


@router.delete("/pipelines/{pipeline_id}", response_model=ApiResponse, summary="删除流水线")
def delete_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    pipeline = _get_pipeline(db, pipeline_id)
    # 历史保护：存在运行记录 → 拒绝删除（FK RESTRICT 的友好前置）
    if db.query(PipelineRun).filter(PipelineRun.pipeline_id == pipeline_id).first():
        raise AppException(code=409, message="流水线存在运行记录，禁止删除", status_code=409)
    db.delete(pipeline)
    db.commit()
    return ApiResponse(data={"id": pipeline_id, "deleted": True})


# ═══════════════════════════════════════════════
# 触发（手动 / webhook / 调度）
# ═══════════════════════════════════════════════

@router.post(
    "/pipelines/{pipeline_id}/trigger",
    response_model=ApiResponse,
    summary="触发流水线（走 Admission）",
)
async def trigger_pipeline(
    pipeline_id: int,
    body: TriggerBody,
    x_pipeline_token: Optional[str] = Header(default=None, alias="X-Pipeline-Token"),
    db: Session = Depends(get_db),
):
    _get_pipeline(db, pipeline_id)  # 存在性校验（404 语义）
    orchestrator = get_orchestrator(db)
    svc = PipelineService(db=db, orchestrator=orchestrator)
    result = await svc.trigger(
        pipeline_id,
        trigger_type=body.trigger_type,
        trigger_detail=body.trigger_detail,
        token=x_pipeline_token,
    )
    return ApiResponse(data=result)


# ═══════════════════════════════════════════════
# 运行视图
# ═══════════════════════════════════════════════

@router.get(
    "/pipelines/{pipeline_id}/runs",
    response_model=ApiResponse,
    summary="流水线运行列表",
)
def list_pipeline_runs(pipeline_id: int, db: Session = Depends(get_db)):
    svc = PipelineService(db=db)
    items = svc.list_runs(pipeline_id)
    return ApiResponse(data={
        "items": [
            {**i, "started_at": str(i["started_at"]) if i["started_at"] else None,
             "finished_at": str(i["finished_at"]) if i["finished_at"] else None}
            for i in items
        ],
        "total": len(items),
    })


@router.get(
    "/pipeline-runs/{run_id}",
    response_model=ApiResponse,
    summary="流水线运行详情（逐阶段 execution 状态/通过率）",
)
def get_pipeline_run(run_id: int, db: Session = Depends(get_db)):
    svc = PipelineService(db=db)
    data = svc.run_detail(run_id)
    for key in ("started_at", "finished_at"):
        if data.get(key):
            data[key] = str(data[key])
    return ApiResponse(data=data)
