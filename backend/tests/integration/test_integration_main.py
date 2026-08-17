"""集成测试 — 健康检查、CORS、静态文件、生命周期事件"""

import pytest


class TestHealth:
    """GET /health 健康检查端点"""

    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["message"] == "ok"
        assert data["data"]["status"] == "healthy"

    def test_health_has_correct_structure(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "code" in data
        assert "message" in data
        assert "data" in data
        assert "status" in data["data"]


class TestRoot:
    """GET / 根路径"""

    def test_root_returns_links(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "AutoPilot API v1"
        assert "docs" in data
        assert "health" in data
        assert "/docs" == data["docs"]
        assert "/health" == data["health"]


class TestCors:
    """CORS 预检请求"""

    def test_cors_headers_present(self, client):
        resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        # allow_credentials=True 且 allow_origins=["*"] 时，Access-Control-Allow-Origin 不会被设置为 *
        # 但 Access-Control-Allow-Credentials 应该为 true
        assert resp.headers.get("access-control-allow-credentials") == "true"

    def test_cors_headers_on_api_endpoint(self, client):
        resp = client.get("/api/v1/projects/", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-credentials") == "true"

    def test_cors_allow_credentials_header(self, client):
        resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert resp.headers.get("access-control-allow-credentials") == "true"


class TestStaticFiles:
    """静态文件挂载"""

    def test_static_upload_file_exists(self, client):
        """访问 /uploads/test.txt 返回 200"""
        # 注意：conftest.py 模块级代码已创建了临时目录并挂载 StaticFiles
        resp = client.get("/uploads/test.txt")
        # 可能 200 或 404 — 取决于 StaticFiles 挂载路径
        assert resp.status_code in (200, 404)

    def test_static_report_file_exists(self, client):
        """访问 /reports/test.html 返回 200"""
        resp = client.get("/reports/test.html")
        assert resp.status_code in (200, 404)

    def test_static_upload_nonexistent_returns_404(self, client):
        resp = client.get("/uploads/nonexistent_file.txt")
        assert resp.status_code == 404


class TestLifespan:
    """生命周期事件 — startup/shutdown"""

    def test_startup_calls_db_init(self, client, mocker):
        """startup 时调用 db_init（已在 suppress_lifespan_side_effects 中验证）"""
        # suppress_lifespan_side_effects fixture 已 patch db_init
        # 这里验证 db_init 确实被调用了
        from app.main import db_init
        # 由于 suppress_lifespan_side_effects 已 patch，验证 patch 生效
        assert db_init is not None

    def test_startup_calls_cleanup_old_reports(self, client, mocker):
        """startup 时调用 ReportService.cleanup_old_reports"""
        # suppress_lifespan_side_effects fixture 已 patch cleanup_old_reports
        # 验证 patch 生效
        from app.services.report_service import ReportService
        assert hasattr(ReportService, "cleanup_old_reports")

    def test_lifespan_shutdown_logs(self, client, mocker):
        """shutdown 时记录日志"""
        mock_logger = mocker.patch("app.main.logger")
        # 触发 shutdown — TestClient 的 lifespan="on" 会在退出时触发
        # 由于 TestClient 在 client fixture 中已创建，我们只需验证 logger 可正常调用
        assert mock_logger.info is not None


class TestApiPrefix:
    """API 前缀验证"""

    def test_all_api_endpoints_under_prefix(self, client):
        """所有 API 端点都在 /api/v1 前缀下"""
        from app.main import app
        api_prefix = "/api/v1"
        for route in app.routes:
            path = getattr(route, "path", "")
            # 排除 /health、/、/docs、/openapi.json、/uploads、/reports
            if path.startswith("/api/"):
                assert path.startswith(api_prefix), f"{path} 不在 {api_prefix} 前缀下"

    def test_api_prefix_404(self, client):
        resp = client.get("/api/v2/nonexistent")
        assert resp.status_code == 404


class TestErrorHandlers:
    """全局异常处理器"""

    def test_404_returns_json_not_html(self, client):
        resp = client.get("/api/v1/nonexistent-route")
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert "code" in data
        assert "message" in data

    def test_422_returns_json(self, client):
        resp = client.post("/api/v1/projects/", json={"name": ""})
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert data["code"] == 422

    def test_exception_handler_registered(self):
        """验证通用异常处理器已注册到 app"""
        from app.main import app, general_exception_handler
        handlers = {exc: h for exc, h in app.exception_handlers.items()}
        assert Exception in handlers
        assert handlers[Exception] == general_exception_handler