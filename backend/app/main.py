"""AutoPilot FastAPI Application — AI 驱动自动化测试平台"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db.database import init as db_init
from app.middlewares import LoggingMiddleware, TimingMiddleware
from app.exceptions import (
    AppException,
    app_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from app.routers import (
    projects_router,
    elements_router,
    cases_router,
    generate_router,
    heal_router,
    executions_router,
    reports_router,
)

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autopilot")


# ── 生命周期 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：初始化数据库；关闭：清理资源"""
    logger.info("正在初始化数据库...")
    db_init()
    logger.info("数据库初始化完成")
    # 清理过期报告
    from app.services.report_service import ReportService
    deleted = ReportService.cleanup_old_reports(max_days=30)
    if deleted:
        logger.info("过期报告清理完成: %s 个文件", deleted)
    yield
    logger.info("应用关闭")


# ── FastAPI 实例 ──

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description="AI 驱动的自动化测试平台 — 用例管理、AI 代码生成、Playwright 执行、自愈修复、报告生成",
    lifespan=lifespan,
)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 自定义中间件 ──
app.add_middleware(TimingMiddleware)
app.add_middleware(LoggingMiddleware)

# ── 静态文件挂载 ──
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")
app.mount("/reports", StaticFiles(directory=settings.REPORT_DIR, html=True), name="reports")

# ── 异常处理器 ──
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# ── 注册路由 ──
api = settings.API_PREFIX  # /api/v1

app.include_router(projects_router, prefix=api)
app.include_router(elements_router, prefix=api)
app.include_router(generate_router, prefix=api)
app.include_router(cases_router, prefix=api)
app.include_router(heal_router, prefix=api)
app.include_router(executions_router, prefix=api)
app.include_router(reports_router, prefix=api)

# ── 健康检查（无前缀） ──

@app.get("/health", tags=["系统"])
def health_check():
    return {"code": 0, "message": "ok", "data": {"status": "healthy"}}


@app.get("/", include_in_schema=False)
def root():
    return {"message": "AutoPilot API v1", "docs": "/docs", "health": "/health"}
