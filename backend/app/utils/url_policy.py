"""URL 策略 — SSRF 双层防护（入口校验 + 执行期网络拦截）

第一层 validate_target_url：项目创建 / 更新 / 健康检查时校验 target_url。
第二层 UrlPolicy + install_network_policy：BrowserContext 级拦截执行期间的
动态导航，覆盖 HTTP/HTTPS（含 redirect / iframe / popup / 静态资源）与
WebSocket；Service Worker 由调用方在 new_context(service_workers="block")
关闭，避免网络拦截盲区。

策略设计（当前项目最适合的 ALLOWLIST 模型）：
  - scheme 仅允许 http / https
  - 默认拒绝危险地址：0.0.0.0、169.254.0.0/16（含云元数据 169.254.169.254）
  - 端口默认仅 80/443，其余端口必须显式加入 allowlist（内网测试服务如 8080
    通过项目 config_json["allowed_ports"] 或环境变量 SSRF_ALLOWED_PORTS 放行）
  - 执行期仅允许：与 target 同 host 的请求 + 显式 allowlist host
    （config_json["allowed_hosts"] / 环境变量 SSRF_ALLOWED_HOSTS），其余一律拦截

容器级 Egress Firewall 属于后续阶段，本模块不涉及。
"""

import ipaddress
import logging
import re
from typing import Optional, Sequence
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger("autopilot.url_policy")

# ── 默认只允许的 scheme ──
ALLOWED_SCHEMES = frozenset({"http", "https"})

# ── 默认端口 ──
DEFAULT_PORTS = frozenset({80, 443})

# ── 永远拒绝的 host（即使 allowlist 也不放行）──
BANNED_HOSTS = frozenset({
    "0.0.0.0",
    "metadata.google.internal",   # GCP / 云元数据
    "169.254.169.254",           # AWS / 通用云元数据
})

# ── 永远拒绝的网段 ──
_BANNED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),        # 链路本地（含云元数据）
    ipaddress.ip_network("255.255.255.255/32"),    # IPv4 广播
)

# ── hostname 格式（RFC 952/1123 简化）──
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$",
    re.IGNORECASE,
)

_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _is_banned_host(hostname: str) -> bool:
    """检查 hostname 是否命中永久黑名单（host 或网段）"""
    lowered = hostname.lower()
    if lowered in BANNED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    for net in _BANNED_NETWORKS:
        if ip in net:
            return True
    return False


def _normalize_allowed_hosts(hosts: Optional[Sequence[str]]) -> set[str]:
    """规范化 allowlist host：去 scheme/路径/端口，支持 . 前缀子域匹配"""
    result: set[str] = set()
    for raw in hosts or []:
        if not raw:
            continue
        host = raw.strip().lower()
        if "://" in host:
            host = urlparse(host).hostname or ""
        elif ":" in host and not host.startswith("["):
            # 去掉可能的 :port 后缀
            host = host.split(":", 1)[0]
        host = host.rstrip(".")
        if host:
            result.add(host)
    return result


def _parse_allowed_hosts(config_json: Optional[dict]) -> set[str]:
    """合并项目级 + 全局环境变量 allowlist host"""
    hosts: set[str] = set()
    if config_json and config_json.get("allowed_hosts"):
        hosts |= _normalize_allowed_hosts(config_json["allowed_hosts"])
    env_hosts = [h for h in (settings.SSRF_ALLOWED_HOSTS or "").split(",") if h.strip()]
    if env_hosts:
        hosts |= _normalize_allowed_hosts(env_hosts)
    return hosts


def _parse_allowed_ports(config_json: Optional[dict]) -> set[int]:
    """合并项目级 + 全局环境变量 allowlist 端口"""
    ports: set[int] = set()
    if config_json and config_json.get("allowed_ports"):
        for p in config_json["allowed_ports"]:
            try:
                ports.add(int(p))
            except (TypeError, ValueError):
                continue
    env_ports = [p for p in (settings.SSRF_ALLOWED_PORTS or "").split(",") if p.strip()]
    for p in env_ports:
        try:
            ports.add(int(p))
        except ValueError:
            continue
    return ports


def _host_matches_allowlist(hostname: str, allowed_hosts: set[str]) -> bool:
    """host 精确匹配，或作为 allowlist 主机的子域（*.example.com）"""
    lowered = hostname.lower().rstrip(".")
    if lowered in allowed_hosts:
        return True
    return any(
        lowered.endswith("." + allowed)
        for allowed in allowed_hosts
        if allowed.startswith(".") or allowed.count(".") > 0
    )


def validate_target_url(
    url: str,
    config_json: Optional[dict] = None,
    allowed_hosts: Optional[Sequence[str]] = None,
    allowed_ports: Optional[Sequence[int]] = None,
) -> Optional[str]:
    """入口层校验 target_url，返回错误消息或 None

    Args:
        url: 项目目标 URL
        config_json: 项目 config_json（可含 allowed_hosts / allowed_ports）
        allowed_hosts: 额外允许的 host（显式传参优先）
        allowed_ports: 额外允许的端口

    Returns:
        None 表示通过；否则为拒绝原因
    """
    raw = (url or "").strip()
    if not raw:
        return "target_url 不能为空"

    parsed = urlparse(raw)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"target_url 仅支持 http/https scheme，当前为: {parsed.scheme or '(缺失)'}"

    hostname = parsed.hostname or ""
    if not hostname:
        return "target_url 缺少有效 hostname"

    if _is_banned_host(hostname):
        return f"target_url 禁止访问受限地址: {hostname}"

    # 端口校验：默认 80/443，其余必须显式放行
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    allowed = DEFAULT_PORTS | _parse_allowed_ports(config_json)
    if allowed_ports:
        allowed |= set(int(p) for p in allowed_ports if p is not None)
    if port not in allowed:
        return f"target_url 端口 {port} 不在允许列表（默认 80/443，可用 config_json.allowed_ports 放行）"

    # hostname 格式校验（IP 或合法域名）
    if not (_IPV4_RE.match(hostname) or _is_ip(hostname) or _HOSTNAME_RE.match(hostname)):
        return f"target_url hostname 格式非法: {hostname}"

    hosts = _parse_allowed_hosts(config_json)
    if allowed_hosts:
        hosts |= _normalize_allowed_hosts(allowed_hosts)
    if hosts and not _host_matches_allowlist(hostname, hosts):
        return f"target_url host {hostname} 不在允许列表内"

    return None


def _is_ip(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


class UrlPolicy:
    """会话级执行期策略：target 同源 + allowlist host + 端口白名单"""

    def __init__(
        self,
        target_url: str,
        config_json: Optional[dict] = None,
        allowed_hosts: Optional[Sequence[str]] = None,
        allowed_ports: Optional[Sequence[int]] = None,
    ) -> None:
        raw = (target_url or "").strip()
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        self.target_host = (parsed.hostname or "").lower().rstrip(".")
        scheme = parsed.scheme or "https"
        self.target_port = parsed.port or (443 if scheme == "https" else 80)

        self._allowed_hosts = _parse_allowed_hosts(config_json)
        if allowed_hosts:
            self._allowed_hosts |= _normalize_allowed_hosts(allowed_hosts)

        self._allowed_ports = DEFAULT_PORTS | _parse_allowed_ports(config_json) | {self.target_port}
        if allowed_ports:
            self._allowed_ports |= set(int(p) for p in allowed_ports if p is not None)

    def is_allowed(self, url: str) -> bool:
        """执行期判定单个请求 URL 是否允许访问"""
        try:
            parsed = urlparse(url)
        except ValueError:
            return False

        # HTTP/HTTPS/WS/WSS 均可（WebSocket 由 route_web_socket 单独拦截）
        if parsed.scheme not in ALLOWED_SCHEMES and parsed.scheme not in ("ws", "wss"):
            return False

        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname or _is_banned_host(hostname):
            return False

        # 仅允许 target 同源 + 显式 allowlist host
        if hostname != self.target_host and not _host_matches_allowlist(hostname, self._allowed_hosts):
            return False

        port = parsed.port or (
            443 if parsed.scheme in ("https", "wss") else 80
        )
        if port not in self._allowed_ports:
            return False

        return True


async def install_network_policy(context, policy: UrlPolicy) -> None:
    """安装 BrowserContext 级网络拦截

    context.route 覆盖所有 HTTP/HTTPS 请求（含重定向、iframe、popup 新页面、
    XHR/fetch 与静态资源）；context.route_web_socket 覆盖 WebSocket 连接。
    Service Worker 需在创建 context 时传 service_workers="block"，由调用方负责。
    """
    # 1. HTTP/HTTPS 请求拦截
    async def _route_handler(route) -> None:
        req = route.request
        if not policy.is_allowed(req.url):
            logger.info("SSRF 拦截: %s", req.url)
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", _route_handler)

    # 2. WebSocket 拦截（Playwright 1.43+ 支持 route_web_socket）
    async def _ws_handler(route) -> None:
        if not policy.is_allowed(route.url):
            logger.info("SSRF 拦截 WebSocket: %s", route.url)
            await route.websocket.close()
        else:
            await route.websocket.connect_to_server()

    try:
        await context.route_web_socket("**/*", _ws_handler)
    except Exception as e:  # 旧版本或不支持时降级，HTTP 拦截仍生效
        logger.warning("WebSocket 拦截不可用: %s", e)
