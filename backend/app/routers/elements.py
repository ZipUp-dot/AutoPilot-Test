"""页面元素路由 — 委托 ElementService / AndroidCrawlService 处理抓取 + 查询 + 清空"""

import logging
import traceback
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.dependencies import get_db, PaginationParams
from app.services.element_service import ElementService
from app.services.android_crawl_service import AndroidCrawlService
from app.schemas import (
    ApiResponse,
    PaginatedData,
    PageElementResponse,
)

logger = logging.getLogger("autopilot.crawl")
router = APIRouter(prefix="/projects/{project_id}/elements", tags=["元素抓取"])


class CrawlRequest(BaseModel):
    max_depth: int = Field(default=1, ge=1, le=3, description="抓取深度，MVP 只抓当前页面")


@router.post("/crawl", response_model=ApiResponse)
async def crawl_elements(project_id: int, body: CrawlRequest, db: Session = Depends(get_db)):
    """触发元素抓取

    使用 Playwright 异步启动无头浏览器（视口 1920x1080），
    访问 target_url + test_path，等待 networkidle 后提取所有可交互元素。
    先清空旧数据，再批量插入新元素。
    """
    svc = ElementService(db)
    try:
        result = await svc.crawl(project_id, max_depth=body.max_depth)
    except Exception as e:
        logger.error(f"元素抓取异常: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        raise

    elements_data = [
        {
            "id": getattr(e, "db_id", 0),
            "element_type": e.element_type,
            "tag_name": e.tag_name,
            "selector": e.selector,
            "text_content": e.text_content,
            "name": e.name,
            "placeholder": e.placeholder,
            "bounding_box": e.bounding_box,
        }
        for e in result.elements
    ]

    return ApiResponse(data={
        "url": result.url,
        "crawled_count": result.crawled_count,
        "elements": elements_data,
        "elapsed_ms": result.elapsed_ms,
    })


@router.post("/crawl/android", response_model=ApiResponse)
def crawl_android_elements(project_id: int, db: Session = Depends(get_db)):
    """触发 Android 屏幕元素抓取

    使用 Appium 连接 Android 设备/模拟器，获取当前屏幕的 XML 页面源码，
    解析提取所有可交互 UI 元素，保存到 PageElement 表（platform=android）。
    """
    svc = AndroidCrawlService(db)
    try:
        result = svc.crawl(project_id)
    except Exception as e:
        logger.error(f"Android 元素抓取异常: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        raise

    if result.error:
        return ApiResponse(
            code=500,
            message=result.error,
            data={"crawled_count": 0, "elapsed_ms": result.elapsed_ms},
        )

    elements_data = [
        {
            "id": getattr(e, "db_id", 0),
            "selector": e.selector,
            "selector_type": e.selector_type,
            "class_name": e.class_name,
            "text": e.text,
            "resource_id": e.resource_id,
            "content_desc": e.content_desc,
            "bounds": e.bounds,
            "clickable": e.clickable,
            "enabled": e.enabled,
        }
        for e in result.elements
    ]

    return ApiResponse(data={
        "crawled_count": result.crawled_count,
        "elements": elements_data,
        "elapsed_ms": result.elapsed_ms,
    })


@router.get("/", response_model=ApiResponse[PaginatedData[PageElementResponse]])
def list_elements(
    project_id: int,
    element_type: str = None,
    keyword: str = None,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
):
    """元素列表（按类型、关键字筛选，分页）"""
    from app.models.project import Project
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        from app.exceptions import NotFoundException
        raise NotFoundException(f"项目 {project_id} 不存在")
    platform = getattr(project, "platform", "web")

    svc = ElementService(db)
    result = svc.list_paginated(
        project_id=project_id,
        platform=platform,
        element_type=element_type,
        keyword=keyword,
        page=pagination.page,
        size=pagination.size,
    )

    items = []
    for e in result.items:
        items.append(PageElementResponse(
            id=getattr(e, "db_id", 0),
            project_id=project_id,
            element_type=e.element_type,
            tag_name=e.tag_name,
            element_id=e.element_id,
            name=e.name,
            class_name=e.class_name,
            selector=e.selector,
            text_content=e.text_content,
            placeholder=e.placeholder,
            is_visible=e.is_visible,
            bounding_box=e.bounding_box if e.bounding_box else None,
            attributes=e.attributes if e.attributes else None,
            created_at=getattr(e, "created_at", None),
        ))

    return ApiResponse(
        data=PaginatedData(
            items=items,
            total=result.total,
            page=result.page,
            size=result.size,
            pages=result.pages,
        )
    )


@router.delete("/", response_model=ApiResponse)
def clear_elements(project_id: int, db: Session = Depends(get_db)):
    """清空项目的所有页面元素"""
    from app.models.project import Project
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        from app.exceptions import NotFoundException
        raise NotFoundException(f"项目 {project_id} 不存在")
    platform = getattr(project, "platform", "web")

    svc = ElementService(db)
    count = svc.clear_all(project_id, platform=platform)
    return ApiResponse(data={"deleted_count": count})
