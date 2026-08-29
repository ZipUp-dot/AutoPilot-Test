"""中间件测试 — TimingMiddleware + LoggingMiddleware"""

import pytest


class TestTimingMiddleware:
    """TimingMiddleware 为每个响应添加 X-Process-Time 头"""

    def test_timing_middleware_adds_header(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "X-Process-Time" in resp.headers
        header_value = resp.headers["X-Process-Time"]
        assert header_value.endswith("ms")
        # Verify it's a valid number
        float(header_value.replace("ms", ""))

    def test_timing_middleware_on_api_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        process_time = resp.headers.get("X-Process-Time", "")
        assert process_time != ""
        # Should be a reasonable positive number
        ms = float(process_time.replace("ms", ""))
        assert ms >= 0


class TestLoggingMiddleware:
    """LoggingMiddleware 不破坏请求处理"""

    def test_logging_middleware_does_not_break_request(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "healthy"

    def test_logging_middleware_with_404(self, client):
        resp = client.get("/api/v1/nonexistent-endpoint")
        assert resp.status_code == 404

    def test_logging_middleware_does_not_leak_sensitive_data(self, client):
        """日志脱敏：敏感头与查询参数不写入访问日志

        访问日志只记录 method / path / status / ip，
        Authorization / Cookie / X-API-Key 等请求头与 query 参数一律不落日志，
        防止 password / token / api_key 等敏感信息进入普通日志。

        说明：不使用 caplog —— test_alembic_migration 运行 alembic 命令时
        env.py 会调用 fileConfig 重置 root logger（level=WARN）并禁用
        未配置的 logger（disable_existing_loggers=True），导致依赖 root
        logger 的 INFO 捕获不稳定；此处直接对目标 logger 附加 handler，
        并显式恢复 disabled / level。
        """
        import logging

        from app.middlewares.logging import logger as access_logger

        records = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _CapturingHandler()
        old_level = access_logger.level
        old_disabled = access_logger.disabled
        access_logger.addHandler(handler)
        access_logger.setLevel(logging.INFO)
        access_logger.disabled = False
        try:
            resp = client.get(
                "/health?api_key=supersecret123&token=querytoken456",
                headers={
                    "Authorization": "Bearer bearer-secret-789",
                    "Cookie": "session=cookie-secret-000",
                    "X-API-Key": "xapi-secret-111",
                },
            )
            assert resp.status_code == 200
        finally:
            access_logger.removeHandler(handler)
            access_logger.setLevel(old_level)
            access_logger.disabled = old_disabled

        assert records, "应产生访问日志"
        log_text = "\n".join(records)
        for secret in (
            "supersecret123",
            "querytoken456",
            "bearer-secret-789",
            "cookie-secret-000",
            "xapi-secret-111",
        ):
            assert secret not in log_text


class TestBothMiddlewares:
    """两个中间件协同工作"""

    def test_both_middlewares_on_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "X-Process-Time" in resp.headers
        data = resp.json()
        assert data["code"] == 0

    def test_both_middlewares_on_api(self, client):
        resp = client.get("/api/v1/projects/")
        assert resp.status_code == 200
        assert "X-Process-Time" in resp.headers
        data = resp.json()
        assert data["code"] == 0