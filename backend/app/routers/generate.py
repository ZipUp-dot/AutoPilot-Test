"""代码生成路由 — 单条生成 + 批量异步 + 最新代码查询"""

import uuid
import threading
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.services.ai_service import AIService, GenerateResult, BatchJob
from app.schemas import ApiResponse
from app.exceptions import AIException

router = APIRouter(tags=["代码生成"])

# ── 批量任务内存追踪 ──
_batch_jobs: dict[str, BatchJob] = {}
_batch_lock = threading.Lock()


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


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    total: int
    completed: int = 0
    failed: int = 0
    progress_pct: float = 0.0


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
    """为单条用例生成可执行的 Playwright Python 异步代码。

    流程:
      1. 查询用例 steps + 项目 elements
      2. 智能匹配步骤 target 到页面元素 selector
      3. 调用 LLM（gpt-4o-mini / deepseek-chat）生成代码
      4. ast.parse 语法校验 + 安全黑名单检查
      5. 存入 generated_codes 表，更新用例状态为 generated
    """
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
    batch_id = str(uuid.uuid4())[:8]
    total = len(body.case_ids)

    job = BatchJob(batch_id=batch_id, total=total, status="running")
    with _batch_lock:
        _batch_jobs[batch_id] = job

    # 后台线程逐条生成
    def _run_batch():
        from app.db.database import SessionLocal
        db_session = SessionLocal()
        try:
            svc = AIService(db_session)
            for cid in body.case_ids:
                try:
                    svc.generate_single(project_id, cid)
                    job.completed += 1
                except Exception:
                    job.failed += 1
            job.status = "completed"
        finally:
            db_session.close()

    t = threading.Thread(target=_run_batch, daemon=True)
    t.start()

    return ApiResponse(data={
        "batch_id": batch_id,
        "total": total,
        "status": "running",
    })


@router.get(
    "/projects/{project_id}/generate-batch/{batch_id}/status",
    response_model=ApiResponse,
    summary="查询批量生成进度",
)
def batch_generate_status(project_id: int, batch_id: str):
    """轮询批量生成任务的完成进度"""
    job = _batch_jobs.get(batch_id)
    if not job:
        return ApiResponse(
            code=404,
            message=f"批次 {batch_id} 不存在",
            data=None,
        )

    total = job.total
    done = job.completed + job.failed
    return ApiResponse(data={
        "batch_id": job.batch_id,
        "status": job.status,
        "total": total,
        "completed": job.completed,
        "failed": job.failed,
        "progress_pct": round(done / total * 100, 1) if total > 0 else 0,
    })


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
