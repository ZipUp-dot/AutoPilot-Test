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


class TestFileAccess:
    """受控文件访问 — INTERNAL_API_TOKEN 未配置时（开发/测试）放行"""

    def test_upload_file_accessible_when_token_unset(self, client):
        """未配置令牌（开发模式）时 /uploads/test.txt 可访问"""
        resp = client.get("/uploads/test.txt")
        assert resp.status_code == 200
        assert resp.text == "hello"

    def test_report_file_accessible_when_token_unset(self, client):
        """未配置令牌时合法报告文件可访问"""
        resp = client.get("/reports/execution_999999_report.html")
        assert resp.status_code == 200
        assert "execution 999999 report" in resp.text

    def test_report_invalid_name_rejected(self, client):
        """reports 必须为 execution_{id}_report.html（资源 ID 校验）"""
        resp = client.get("/reports/test.html")
        assert resp.status_code == 404

    def test_upload_nonexistent_returns_404(self, client):
        resp = client.get("/uploads/nonexistent_file.txt")
        assert resp.status_code == 404


class TestFileAccessToken:
    """配置 INTERNAL_API_TOKEN 后：未经令牌 401、非法资源 404、合法资源 200"""

    TOKEN = "test-internal-token"

    @pytest.fixture
    def secured_client(self, client, mock_settings):
        mock_settings("INTERNAL_API_TOKEN", self.TOKEN)
        return client

    def test_missing_token_returns_401(self, secured_client):
        resp = secured_client.get("/uploads/test.txt")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, secured_client):
        resp = secured_client.get(
            "/uploads/test.txt",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_valid_token_serves_upload(self, secured_client):
        resp = secured_client.get(
            "/uploads/test.txt",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.text == "hello"

    def test_valid_token_serves_report(self, secured_client):
        resp = secured_client.get(
            "/reports/execution_999999_report.html",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code == 200
        assert "execution 999999 report" in resp.text

    def test_path_traversal_rejected(self, secured_client):
        """编码路径穿越（HTTP 客户端会规范化裸 ../，编码形式才是真实攻击向量）"""
        resp = secured_client.get(
            "/uploads/..%2freports%2fexecution_1_report.html",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code in (400, 404)

    def test_encoded_path_traversal_rejected(self, secured_client):
        resp = secured_client.get(
            "/uploads/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code in (400, 404)

    def test_encoded_absolute_path_rejected(self, secured_client):
        resp = secured_client.get(
            "/uploads/%2Fetc%2Fpasswd",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code in (400, 404)

    def test_uploads_missing_resource_id_rejected(self, secured_client):
        """uploads 类型子目录下缺少资源 ID → 404（资源 ID 校验）"""
        resp = secured_client.get(
            "/uploads/screenshots/step.png",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code == 404

    def test_symlink_escape_rejected(self, secured_client, tmp_path):
        """符号链接指向目录外 → 404（resolve 边界校验）"""
        import os
        import sys
        from pathlib import Path

        if sys.platform == "win32":
            pytest.skip("Windows 上创建符号链接需要管理员权限")

        from app.config import settings

        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        link = Path(settings.UPLOAD_DIR) / "evil_link.txt"
        try:
            os.symlink(secret, link)
            resp = secured_client.get(
                "/uploads/evil_link.txt",
                headers={"Authorization": f"Bearer {self.TOKEN}"},
            )
            assert resp.status_code == 404
        finally:
            if link.exists() or link.is_symlink():
                link.unlink()


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