"""mock_service：handler 行为 + delay_ms（AC-04）+ 安装点 — RED 先行

Spec §5 / §6 / §11：
  - 匹配规则 → 返回定制响应（status/headers/body）；
  - 未匹配 → 放行真实请求（continue_，不阻断）；
  - delay_ms 生效（墙钟 ≥ delay，mock 时钟）；
  - 仅 enabled server 注册拦截；1 个 server → 1 个 route handler。
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.mock import MockRule, MockServer
from app.services.mock_service import build_mock_handler, install_mock_for_execution


class _Server:
    def __init__(self, base_path="/mock", enabled=True):
        self.id = 1
        self.base_path = base_path
        self.enabled = enabled


class _Rule:
    def __init__(self, method="GET", pattern="/api/users/:id", status_code=200,
                 body='{"ok":true}', headers='{"X-Mock":"1"}', delay_ms=0, enabled=True):
        self.id = 1
        self.method = method
        self.path_pattern = pattern
        self.status_code = status_code
        self.response_body = body
        self.response_headers = headers
        self.delay_ms = delay_ms
        self.enabled = enabled


def _route():
    # fallback()：交回既有 SSRF 策略链（continue_ 会跳过先注册的 handler，禁止）
    return SimpleNamespace(fulfill=AsyncMock(), fallback=AsyncMock(), continue_=AsyncMock())


def _request(method="GET", url="https://dep.example.com/mock/api/users/7"):
    return SimpleNamespace(method=method, url=url)


class TestHandler:
    async def test_matched_returns_custom_response(self):
        route = _route()
        handler = build_mock_handler(_Server(), [_Rule()])
        outcome = await handler(route, _request())

        assert outcome == "fulfilled"
        route.fallback.assert_not_awaited()
        route.fulfill.assert_awaited_once()
        kwargs = route.fulfill.call_args.kwargs
        assert kwargs["status"] == 200
        assert json.loads(kwargs["body"]) == {"ok": True}
        assert kwargs["headers"]["X-Mock"] == "1"

    async def test_unmatched_passes_through(self):
        route = _route()
        handler = build_mock_handler(_Server(), [_Rule()])
        outcome = await handler(route, _request(url="https://dep.example.com/mock/other/x"))

        assert outcome == "passed"
        route.fulfill.assert_not_awaited()
        route.fallback.assert_awaited_once()
        route.continue_.assert_not_awaited()

    async def test_method_mismatch_passes_through(self):
        route = _route()
        handler = build_mock_handler(_Server(), [_Rule(method="POST")])
        outcome = await handler(route, _request(method="GET"))
        assert outcome == "passed"
        route.fallback.assert_awaited_once()
        route.continue_.assert_not_awaited()

    async def test_delay_ms_awaited_with_seconds(self, mocker):
        sleep_mock = mocker.patch("app.services.mock_service.asyncio.sleep",
                                  new=AsyncMock())
        route = _route()
        handler = build_mock_handler(_Server(), [_Rule(delay_ms=50)])
        await handler(route, _request())

        sleep_mock.assert_awaited_once_with(0.05)
        assert route.fulfill.call_args.kwargs["status"] == 200

    async def test_delay_ms_wall_clock_at_least_delay(self):
        route = _route()
        handler = build_mock_handler(_Server(), [_Rule(delay_ms=40)])
        start = time.monotonic()
        await handler(route, _request())
        elapsed = time.monotonic() - start
        assert elapsed >= 0.035, f"delay 未生效: {elapsed:.4f}s"


class TestInstall:
    async def test_install_registers_one_route(self, db_session, sample_project):
        server = MockServer(project_id=sample_project.id, name="dep",
                            base_path="/mock", enabled=True)
        db_session.add(server)
        db_session.commit()
        db_session.refresh(server)
        db_session.add(MockRule(server_id=server.id, method="GET",
                                path_pattern="/api/users/:id", status_code=200,
                                response_body='{"ok":true}', delay_ms=0, enabled=True))
        db_session.commit()

        context = SimpleNamespace(route=AsyncMock())
        n = await install_mock_for_execution(context, db_session, server.id)

        assert n == 1
        context.route.assert_awaited_once()
        assert context.route.call_args.args[0] == "**/mock/**"

    async def test_install_skips_disabled_server(self, db_session, sample_project):
        server = MockServer(project_id=sample_project.id, name="dep",
                            base_path="/mock", enabled=False)
        db_session.add(server)
        db_session.commit()
        db_session.refresh(server)

        context = SimpleNamespace(route=AsyncMock())
        n = await install_mock_for_execution(context, db_session, server.id)

        assert n == 0
        context.route.assert_not_awaited()

    async def test_install_skips_unknown_server(self, db_session):
        context = SimpleNamespace(route=AsyncMock())
        n = await install_mock_for_execution(context, db_session, 999999)
        assert n == 0
        context.route.assert_not_awaited()


class TestSourceNoAppiumTouch:
    """AC-05 Source 佐证：mock 服务不引用 Android/Appium 实现（不变量 #3）"""

    def test_source_has_no_appium_dependency(self):
        import inspect
        from app.services import mock_service as mod

        src = inspect.getsource(mod)
        for banned in ("appium_service", "AppiumService", "import appium",
                       "from appium", "appium_caps", "appium_code_injector"):
            assert banned not in src, f"mock 服务不得依赖 Android 域: {banned}"
