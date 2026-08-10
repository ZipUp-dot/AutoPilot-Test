"""FastAPI 依赖注入 — DB 会话 + 分页辅助 + 编排器注入"""

import math
from typing import Generator, Any
from sqlalchemy.orm import Session, Query
from fastapi import Depends, Query as FastQuery
from app.db.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话（每个请求一个事务）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_orchestrator(db: Session = Depends(get_db)):
    """获取编排器实例（依赖注入：AI + Playwright + Report）

    每次请求创建新的编排器实例，注入独立 DB 会话的 service 实例。
    """
    from app.services.ai_service import AIService
    from app.services.playwright_service import PlaywrightService
    from app.services.report_service import ReportService
    from app.services.orchestrator import TestOrchestrator

    return TestOrchestrator(
        ai_service=AIService(db),
        playwright_service=PlaywrightService(db),
        report_service=ReportService(db),
    )


def get_project_or_404(project_id: int, db: Session = Depends(get_db)):
    """获取项目或抛出 404"""
    from app.models.project import Project
    from app.exceptions import NotFoundException

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundException(f"项目 {project_id} 不存在")
    return project


class PaginationParams:
    """分页查询参数依赖"""

    def __init__(
        self,
        page: int = FastQuery(default=1, ge=1, description="页码"),
        size: int = FastQuery(default=20, ge=1, le=1000, description="每页数量"),
    ):
        self.page = page
        self.size = size
        self.offset = (page - 1) * size


def paginate(query: Query, params: PaginationParams) -> dict[str, Any]:
    """对 SQLAlchemy Query 执行分页，返回 {items, total, page, size, pages}"""
    total = query.count()
    items = query.offset(params.offset).limit(params.size).all()
    return {
        "items": items,
        "total": total,
        "page": params.page,
        "size": params.size,
        "pages": math.ceil(total / params.size) if total > 0 else 0,
    }
