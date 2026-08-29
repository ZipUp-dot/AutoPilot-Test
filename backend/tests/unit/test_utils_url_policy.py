"""URL 策略单元测试 — SSRF 入口校验 + 执行期网络拦截"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.utils.url_policy import (
    UrlPolicy,
    validate_target_url,
    install_network_policy,
)


# ═══════════════════════════════════════════════
# validate_target_url — 入口校验
# ═══════════════════════════════════════════════

class TestValidateTargetUrl:
    """第一层：项目 target_url 创建/更新时校验"""

    def test_https_url_passes(self):
        assert validate_target_url("https://example.com") is None

    def test_http_url_passes(self):
        assert validate_target_url("http://example.com") is None

    def test_url_with_path_and_query_passes(self):
        assert validate_target_url("https://example.com/login?next=/") is None

    def test_empty_url_rejected(self):
        assert validate_target_url("") is not None
        assert validate_target_url(None) is not None

    def test_non_http_scheme_rejected(self):
        assert "scheme" in validate_target_url("ftp://example.com")
        assert "scheme" in validate_target_url("file:///etc/passwd")
        assert "scheme" in validate_target_url("gopher://example.com")

    def test_missing_scheme_rejected(self):
        assert "scheme" in validate_target_url("example.com")

    def test_metadata_ip_rejected(self):
        assert "禁止" in validate_target_url("http://169.254.169.254/latest/meta-data")

    def test_zero_ip_rejected(self):
        assert validate_target_url("http://0.0.0.0") is not None

    def test_link_local_network_rejected(self):
        assert validate_target_url("http://169.254.5.5") is not None

    def test_metadata_hostname_rejected(self):
        assert validate_target_url("http://metadata.google.internal") is not None

    def test_non_default_port_rejected(self):
        assert "端口" in validate_target_url("https://example.com:8080")

    def test_non_default_port_allowed_via_config(self):
        result = validate_target_url(
            "https://example.com:8080",
            config_json={"allowed_ports": [8080]},
        )
        assert result is None

    def test_non_default_port_allowed_via_arg(self):
        result = validate_target_url("https://example.com:8080", allowed_ports=[8080])
        assert result is None

    def test_invalid_hostname_rejected(self):
        assert "hostname" in validate_target_url("http://inv alid host")

    def test_allowlist_host_required(self):
        result = validate_target_url(
            "https://internal.example",
            config_json={"allowed_hosts": ["api.example.com"]},
        )
        assert "不在允许列表" in result

    def test_allowlist_host_allowed(self):
        result = validate_target_url(
            "https://internal.example",
            config_json={"allowed_hosts": ["internal.example"]},
        )
        assert result is None

    def test_allowlist_subdomain_match(self):
        result = validate_target_url(
            "https://api.internal.example",
            config_json={"allowed_hosts": ["internal.example"]},
        )
        assert result is None


# ═══════════════════════════════════════════════
# UrlPolicy — 执行期请求判定
# ═══════════════════════════════════════════════

class TestUrlPolicy:
    """第二层：BrowserContext 拦截时对每个请求 URL 判定"""

    def setup_method(self):
        self.policy = UrlPolicy("https://example.com")

    # ── 同源放行 ──

    def test_same_origin_allowed(self):
        assert self.policy.is_allowed("https://example.com/") is True
        assert self.policy.is_allowed("https://example.com/static/app.js") is True
        assert self.policy.is_allowed("https://example.com/api?x=1") is True

    def test_http_default_port_allowed(self):
        assert self.policy.is_allowed("http://example.com/") is True

    def test_redirect_same_origin_allowed(self):
        assert self.policy.is_allowed("https://example.com/redirect-target") is True

    # ── 跨域拒绝 ──

    def test_cross_origin_rejected(self):
        """redirect / 点击跳转 / iframe / popup 到黑名单 host → 拒绝"""
        assert self.policy.is_allowed("https://evil.com/") is False
        assert self.policy.is_allowed("http://169.254.169.254/") is False
        assert self.policy.is_allowed("https://internal.corp/") is False

    def test_subdomain_not_allowed_without_allowlist(self):
        assert self.policy.is_allowed("https://sub.example.com/") is False

    def test_non_default_port_rejected(self):
        assert self.policy.is_allowed("https://example.com:8080/") is False

    def test_banned_scheme_rejected(self):
        assert self.policy.is_allowed("file:///etc/passwd") is False
        assert self.policy.is_allowed("ftp://example.com/") is False

    # ── allowlist 放行 ──

    def test_allowlist_host_allowed(self):
        policy = UrlPolicy(
            "https://example.com",
            config_json={"allowed_hosts": ["cdn.example.net"]},
        )
        assert policy.is_allowed("https://cdn.example.net/lib.js") is True

    def test_allowlist_port_allowed(self):
        policy = UrlPolicy(
            "https://example.com",
            config_json={"allowed_ports": [8443]},
        )
        assert policy.is_allowed("https://example.com:8443/api") is True

    def test_allowlist_host_and_port(self):
        policy = UrlPolicy(
            "https://example.com",
            config_json={"allowed_hosts": ["cdn.example.net"], "allowed_ports": [8443]},
        )
        assert policy.is_allowed("https://cdn.example.net:8443/x") is True

    # ── WebSocket ──

    def test_websocket_same_origin_allowed(self):
        assert self.policy.is_allowed("wss://example.com/socket") is True

    def test_websocket_cross_origin_rejected(self):
        assert self.policy.is_allowed("wss://evil.com/socket") is False


# ═══════════════════════════════════════════════
# install_network_policy — BrowserContext 拦截
# ═══════════════════════════════════════════════

class TestInstallNetworkPolicy:
    """context.route 与 route_web_socket 注册及 handler 行为"""

    async def _install(self, policy):
        registered = {}
        context = MagicMock()

        async def fake_route(pattern, handler):
            registered.setdefault(pattern, []).append(handler)

        context.route = AsyncMock(side_effect=fake_route)
        context.route_web_socket = AsyncMock(side_effect=fake_route)
        await install_network_policy(context, policy)
        return context, registered

    class FakeRoute:
        def __init__(self, url):
            self.url = url
            self.request = MagicMock()
            self.request.url = url
            self.abort = AsyncMock()
            self.continue_ = AsyncMock()
            self.websocket = MagicMock()
            self.websocket.close = AsyncMock()
            self.websocket.connect_to_server = AsyncMock()

    async def test_registers_http_and_ws_handlers(self):
        _, registered = await self._install(UrlPolicy("https://example.com"))
        assert len(registered["**/*"]) == 2  # HTTP handler + WebSocket handler

    async def test_allowed_request_continues(self):
        _, registered = await self._install(UrlPolicy("https://example.com"))
        handler = registered["**/*"][0]
        route = self.FakeRoute("https://example.com/static/app.js")
        await handler(route)
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()

    async def test_blocked_request_aborted(self):
        """静态资源 / iframe / popup 请求黑名单 → abort"""
        _, registered = await self._install(UrlPolicy("https://example.com"))
        handler = registered["**/*"][0]
        route = self.FakeRoute("https://evil.com/pixel.png")
        await handler(route)
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()

    async def test_redirect_to_blacklist_aborted(self):
        _, registered = await self._install(UrlPolicy("https://example.com"))
        handler = registered["**/*"][0]
        route = self.FakeRoute("https://169.254.169.254/ssrf")
        await handler(route)
        route.abort.assert_awaited_once()

    async def test_websocket_allowed_connects(self):
        _, registered = await self._install(UrlPolicy("https://example.com"))
        ws_handler = registered["**/*"][1]
        route = self.FakeRoute("wss://example.com/socket")
        await ws_handler(route)
        route.websocket.connect_to_server.assert_awaited_once()
        route.websocket.close.assert_not_awaited()

    async def test_websocket_blocked_closed(self):
        _, registered = await self._install(UrlPolicy("https://example.com"))
        ws_handler = registered["**/*"][1]
        route = self.FakeRoute("wss://evil.com/socket")
        await ws_handler(route)
        route.websocket.close.assert_awaited_once()
        route.websocket.connect_to_server.assert_not_awaited()

    async def test_route_web_socket_unavailable_degrades(self):
        """旧版本不支持 route_web_socket → 不抛异常，HTTP 拦截仍生效"""
        registered = {}
        context = MagicMock()

        async def fake_route(pattern, handler):
            registered.setdefault(pattern, []).append(handler)

        context.route = AsyncMock(side_effect=fake_route)
        context.route_web_socket = AsyncMock(side_effect=RuntimeError("unsupported"))

        await install_network_policy(context, UrlPolicy("https://example.com"))
        assert len(registered["**/*"]) == 1  # 仅 HTTP handler
