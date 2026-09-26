"""代码生成路由 — 单条生成 + 批量异步 + 最新代码查询

批量生成统一收敛到 BatchGenerateService（进程内单例），
与 Orchestrator run_full_pipeline 共享同一实现（双入口统一）。
本文件保持薄路由：只调用 Service，保留接口形状，status 响应升级为 case-level 明细。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.services.ai_service import AIService
from app.services.batch_generate_service import batch_generate_service
from app.schemas import ApiResponse
from app.exceptions import AIException, NotFoundException, ValidationException

router = APIRouter(tags=["代码生成"])


class BatchGenerateBody(BaseModel):
    case_ids: list[int] = Field(..., min_length=1, description="要生成的用例 ID 列表")


class GenerateResponse(BaseModel):
    code_id: int
    code_content: str
    is_valid: bool
    syntax_error: str | None = None
    ai_model: str | None = None


class BatchGenerateResponse(BaseModel):
    batch_id: str
    total: int
    status: str = "running"


class LatestCodeResponse(BaseModel):
    code_id: int
    code_content: str
    is_valid: bool
    syntax_error: str | None = None
    is_healed: bool = False
    ai_model: str | None = None
    created_at: str | None = None


# ═══════════════════════════════════════════════
# 单条生成
# ═══════════════════════════════════════════════

@router.post(
    "/projects/{project_id}/cases/{case_id}/generate",
    response_model=ApiResponse,
    summary="为单条用例生成 Playwright 代码",
)
def generate_code(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
):
    """为单条用例生成可执行的 Playwright Python 异步代码。"""
    svc = AIService(db)
    result = svc.generate_single(project_id, case_id)
    return ApiResponse(data={
        "code_id": result.code_id,
        "code_content": result.code_content,
        "is_valid": result.is_valid,
        "syntax_error": result.syntax_error,
        "ai_model": result.ai_model,
    })


# ═══════════════════════════════════════════════
# 批量生成
# ═══════════════════════════════════════════════

@router.post(
    "/projects/{project_id}/cases/generate-batch",
    response_model=ApiResponse,
    summary="批量异步生成用例代码",
)
def batch_generate(
    project_id: int,
    body: BatchGenerateBody,
    db: Session = Depends(get_db),
):
    """批量生成 Playwright 代码（后台异步执行，不阻塞接口）。

    返回 batch_id，通过 GET /generate-batch/{batch_id}/status 轮询进度。
    """
    try:
        batch_id = batch_generate_service.create_job(project_id, body.case_ids, db=db)
    except ValidationException as e:
        return ApiResponse(code=422, message=str(e), data=None)
    except NotFoundException as e:
        return ApiResponse(code=404, message=str(e), data=None)

    return ApiResponse(data={
        "batch_id": batch_id,
        "total": len(dict.fromkeys(body.case_ids)),
        "status": "running",
    })


@router.get(
    "/projects/{project_id}/generate-batch/{batch_id}/status",
    response_model=ApiResponse,
    summary="查询批量生成进度",
)
def batch_generate_status(project_id: int, batch_id: str):
    """轮询批量生成任务的完成进度（case-level 明细）"""
    try:
        data = batch_generate_service.status(project_id, batch_id)
    except NotFoundException as e:
        return ApiResponse(code=404, message=str(e), data=None)

    return ApiResponse(data=data)


# ═══════════════════════════════════════════════
# 最新代码查询
# ═══════════════════════════════════════════════

@router.get(
    "/projects/{project_id}/cases/{case_id}/code",
    response_model=ApiResponse,
    summary="获取用例最新生成的代码",
)
def get_latest_code(project_id: int, case_id: int, db: Session = Depends(get_db)):
    """获取指定用例最新生成的代码（含 healed 版本信息）"""
    svc = AIService(db)
    data = svc.get_latest_code(project_id, case_id)
    return ApiResponse(data=data)