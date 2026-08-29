"""路由聚合测试 — 验证 app/routers/__init__.py 正确导出所有路由"""


class TestRouterImport:
    """所有 router 模块可导入且包含 router 属性"""

    def test_import_all_routers(self):
        """__all__ 中所有路由模块均可导入且包含 router 属性"""
        from app.routers import __all__ as router_names

        assert len(router_names) == 8
        for name in router_names:
            module = __import__(f"app.routers.{name.replace('_router', '')}", fromlist=[name])
            assert hasattr(module, "router"), f"{name} 缺少 router 属性"

    def test_routers_init_exports_all(self):
        """app.routers.__init__ 导出所有 7 个 router 名称"""
        from app.routers import __all__

        expected = [
            "projects_router",
            "elements_router",
            "cases_router",
            "generate_router",
            "heal_router",
            "executions_router",
            "reports_router",
        ]
        for name in expected:
            assert name in __all__, f"{name} 不在 __all__ 中"

    def test_each_router_is_apirouter(self):
        """每个导出的 router 都是 APIRouter 实例"""
        from app.routers import projects_router, elements_router, cases_router
        from app.routers import generate_router, heal_router, executions_router, reports_router
        from fastapi import APIRouter

        for router in [
            projects_router, elements_router, cases_router,
            generate_router, heal_router, executions_router, reports_router,
        ]:
            assert isinstance(router, APIRouter), f"{router} 不是 APIRouter 实例"

    def test_main_app_includes_all_routers(self):
        """app.main 的 app 对象包含所有路由端点"""
        from app.main import app

        routes = [r.path for r in app.routes]
        # 验证每个路由前缀下至少有一个端点
        assert any("/api/v1/projects" in r for r in routes)
        assert any("/api/v1/projects/{" in r for r in routes)
        assert any("/api/v1/executions" in r for r in routes)
        assert any("/api/v1/executions/{" in r for r in routes)
        assert any("/health" in r for r in routes)
        assert any("/api/v1/projects/{project_id}/cases" in r for r in routes)
        assert any("/api/v1/projects/{project_id}/elements" in r for r in routes)
        assert any("/api/v1/executions/{execution_id}/reports" in r for r in routes)