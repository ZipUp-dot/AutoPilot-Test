"""自定义异常类 + 全局异常处理器 — 统一返回 {"code":x,"message":"...","data":null}"""

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppException(Exception):
    """应用异常基类"""

    def __init__(self, code: int = -1, message: str = "error", status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundException(AppException):
    """资源不存在"""
    def __init__(self, message: str = "资源不存在"):
        super().__init__(code=404, message=message, status_code=404)


class ValidationException(AppException):
    """请求参数校验失败"""
    def __init__(self, message: str = "参数校验失败"):
        super().__init__(code=422, message=message, status_code=422)


class AIException(AppException):
    """AI 服务异常

    统一携带 error_type（用于结构化日志/Batch 层分类）与 retryable（是否可重试）。
    """
    def __init__(self, message: str = "AI 服务异常", *,
                 error_type: str = "ai_error", retryable: bool = False):
        super().__init__(code=500, message=message, status_code=500)
        self.error_type = error_type
        self.retryable = retryable


class DeadlineExceeded(AIException):
    """case_deadline 到期（wall-clock 或 remaining<=0），non-retryable"""

    def __init__(self, message: str = "AI 调用超过截止时间"):
        super().__init__(message=message, error_type="deadline_exceeded", retryable=False)


class PlaywrightException(AppException):
    """Playwright 执行异常"""
    def __init__(self, message: str = "浏览器操作异常"):
        super().__init__(code=500, message=message, status_code=500)


class SecurityException(AppException):
    """安全校验异常（鉴权/越权）"""
    def __init__(self, message: str = "安全校验失败"):
        super().__init__(code=403, message=message, status_code=403)


class SecurityError(SecurityException):
    """执行环境越界错误（如截图路径越界）

    SecurityException 的子类：捕获 SecurityException 的既有逻辑同样生效，
    同时允许按越界语义单独捕获 SecurityError。
    """
    def __init__(self, message: str = "执行环境越界"):
        super().__init__(message=message)


class UnauthorizedException(AppException):
    """未授权（令牌缺失/无效）"""
    def __init__(self, message: str = "未授权访问"):
        super().__init__(code=401, message=message, status_code=401)


class SealedExecutionError(AppException):
    """Execution 已封存（终态 status 即数据库封存边界）

    P0-10：Execution 进入终态后，应用层拒绝任何后续 Manifest/Runtime/Step/
    HealRecord 修改。无需独立 sealed/is_sealed 状态字段——终态 status 本身就是
    封存标记，Seal 与终态是同一个原子事实。
    """
    def __init__(self, message: str = "执行已封存（终态），禁止修改"):
        super().__init__(code=409, message=message, status_code=409)


# ═══════════════════════════════════════════════
# 全局异常处理器
# ═══════════════════════════════════════════════

async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail, "data": None},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors: list[str] = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        errors.append(f"{field}: {error['msg']}")
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": "; ".join(errors), "data": None},
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": f"服务器内部错误: {str(exc)}", "data": None},
    )
