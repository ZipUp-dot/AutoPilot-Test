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