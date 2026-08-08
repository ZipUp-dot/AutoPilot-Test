"""请求日志中间件：记录 method, path, status_code, duration_ms, client_ip"""

import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("autopilot.access")


class LoggingMiddleware(BaseHTTPMiddleware):
    """记录每个请求"""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        client_ip = request.client.host if request.client else "unknown"
        logger.info(
            "[%s] %s %s -> %d (%.2fms)",
            client_ip,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
