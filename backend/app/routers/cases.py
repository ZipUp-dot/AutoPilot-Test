"""测试用例路由 — 委托 CaseService 处理导入/查询/删除"""

from fastapi import APIRouter, Depends, UploadFile, File, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.dependencies import get_db, PaginationParams
from app.services.case_service import CaseService
from app.schemas import ApiResponse, PaginatedData
from app.exceptions import ValidationException

router = APIRouter(prefix="/projects/{project_id}/cases", tags=["用例管理"])


class BatchDeleteBody(BaseModel):
    ids: list[int] = Field(..., min_length=1)


# ═══════════════════════════════════════════════
# 导入
# ═══════════════════════════════════════════════

@router.post("/import", response_model=ApiResponse)
async def import_excel(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传 Excel 批量导入用例

    文件校验：
      - 仅接受 .xlsx / .xls 格式
      - 文件大小 ≤ 10MB
      - 保存到 uploads/excels/{project_id}/{timestamp}_{filename}

    智能解析：
      - 自动匹配中文/英文列名（含模糊匹配）
      - 支持 3 种步骤格式：JSON 数组 / 纯文本行 / 操作+对象+数据三列
      - action 必须为标准枚举值（navigate/fill/click/select/hover/assert_text/assert_visible/screenshot/wait）
    """
    if not file.filename:
        raise ValidationException("请选择文件")

    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise ValidationException("仅支持 .xlsx / .xls 格式文件")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValidationException("文件大小不能超过 10MB")

    svc = CaseService(db)
    result = svc.import_excel(project_id, content, file.filename)

    return ApiResponse(data={
        "total": result.total,
        "success": result.success,
        "failed": result.failed,
        "errors": result.errors,
    })


# ═══════════════════════════════════════════════
# 列表
# ═══════════════════════════════════════════════

@router.get("/", response_model=ApiResponse)
def list_cases(
    project_id: int,
    status: str = Query(default=None, description="pending/imported/generated"),
    priority: str = Query(default=None, description="P0/P1/P2/P3"),
    keyword: str = Query(default=None, description="搜索关键字（名称/编号/步骤）"),
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
):
    """用例列表（分页 + 筛选 + 关键字搜索）

    列表中 steps 只返回前 3 步摘要，详情请调用 GET /{case_id}
    """
    svc = CaseService(db)
    result = svc.list_paginated(
        project_id=project_id,
        page=pagination.page,
        size=pagination.size,
        status=status,
        priority=priority,
        keyword=keyword,
    )

    items = [
        {
            "id": item.id,
            "project_id": item.project_id,
            "case_name": item.case_name,
            "case_no": item.case_no,
            "priority": item.priority,
            "pre_condition": item.pre_condition,
            "steps": item.steps_summary,
            "expected_result": item.expected_result,
            "source_excel": item.source_excel,
            "excel_row": item.excel_row,
            "status": item.status,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        for item in result.items
    ]

    return ApiResponse(data=PaginatedData(
        items=items,
        total=result.total,
        page=result.page,
        size=result.size,
        pages=result.pages,
    ))


# ═══════════════════════════════════════════════
# 详情
# ═══════════════════════════════════════════════

@router.get("/{case_id}", response_model=ApiResponse)
def get_case_detail(project_id: int, case_id: int, db: Session = Depends(get_db)):
    """获取用例详情（含完整 steps JSON）"""
    svc = CaseService(db)
    detail = svc.get_detail(project_id, case_id)
    return ApiResponse(data={
        "id": detail.id,
        "project_id": detail.project_id,
        "case_name": detail.case_name,
        "case_no": detail.case_no,
        "priority": detail.priority,
        "pre_condition": detail.pre_condition,
        "steps": detail.steps,
        "expected_result": detail.expected_result,
        "source_excel": detail.source_excel,
        "excel_row": detail.excel_row,
        "status": detail.status,
        "created_at": detail.created_at,
        "updated_at": detail.updated_at,
    })


# ═══════════════════════════════════════════════
# 删除
# ═══════════════════════════════════════════════

@router.delete("/{case_id}", response_model=ApiResponse)
def delete_case(project_id: int, case_id: int, db: Session = Depends(get_db)):
    """删除单条用例（级联删除 generated_codes, execution_steps）"""
    svc = CaseService(db)
    svc.delete_one(project_id, case_id)
    return ApiResponse(data={"deleted": case_id})


@router.delete("/", response_model=ApiResponse)
def batch_delete_cases(
    project_id: int,
    body: BatchDeleteBody,
    db: Session = Depends(get_db),
):
    """批量删除用例"""
    svc = CaseService(db)
    count = svc.delete_batch(project_id, body.ids)
    return ApiResponse(data={"deleted_count": count})
