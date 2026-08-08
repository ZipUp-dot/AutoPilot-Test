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
    """AI 服务异常"""
    def __init__(self, message: str = "AI 服务异常"):
        super().__init__(code=500, message=message, status_code=500)


class PlaywrightException(AppException):
    """Playwright 执行异常"""
    def __init__(self, message: str = "浏览器操作异常"):
        super().__init__(code=500, message=message, status_code=500)


class SecurityException(AppException):
    """安全校验异常（鉴权/越权）"""
    def __init__(self, message: str = "安全校验失败"):
        super().__init__(code=403, message=message, status_code=403)


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
