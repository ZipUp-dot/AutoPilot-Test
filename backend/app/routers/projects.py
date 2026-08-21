"""项目路由 — 委托 ProjectService 处理业务逻辑"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.dependencies import get_db, PaginationParams
from app.services.project_service import ProjectService
from app.schemas import (
    ApiResponse,
    PaginatedData,
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectListItem,
)

router = APIRouter(prefix="/projects", tags=["项目管理"])


@router.get("/", response_model=ApiResponse[PaginatedData[ProjectListItem]])
def list_projects(
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
):
    """项目列表（分页）"""
    svc = ProjectService(db)
    result = svc.list_paginated(page=pagination.page, size=pagination.size)
    items = [ProjectListItem(**item) for item in result.items]
    return ApiResponse(
        data=PaginatedData(
            items=items,
            total=result.total,
            page=result.page,
            size=result.size,
            pages=result.pages,
        )
    )


@router.post("/", response_model=ApiResponse[ProjectResponse])
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    """创建项目"""
    svc = ProjectService(db)
    project = svc.create(
        name=body.name,
        target_url=body.target_url,
        test_path=body.test_path,
        browser_type=body.browser_type,
        headless=bool(body.headless),
        platform=body.platform,
        config_json=body.config_json,
    )
    return ApiResponse(data=ProjectResponse.model_validate(project))


@router.get("/{project_id}", response_model=ApiResponse[ProjectResponse])
def get_project(project_id: int, db: Session = Depends(get_db)):
    """项目详情"""
    svc = ProjectService(db)
    project = svc.get_or_404(project_id)
    return ApiResponse(data=ProjectResponse.model_validate(project))


@router.put("/{project_id}", response_model=ApiResponse[ProjectResponse])
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db)):
    """更新项目"""
    svc = ProjectService(db)
    update_data = body.model_dump(exclude_unset=True)
    project = svc.update(project_id, **update_data)
    return ApiResponse(data=ProjectResponse.model_validate(project))


@router.delete("/{project_id}", response_model=ApiResponse)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """删除项目（级联删除关联数据）"""
    svc = ProjectService(db)
    svc.delete(project_id)
    return ApiResponse(data={"deleted": True})
