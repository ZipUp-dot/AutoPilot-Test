"""Mock 服务 — 规则匹配引擎 + Playwright route 拦截安装点

PROJ-V20-MOCK（F3）。设计（Spec §5 / §6 / §8）：
  - **无新端口、无新进程**：Mock 由 Playwright `context.route` 拦截实现
    （BrowserContext 级，单 worker 内安全）；MockServer 只是逻辑命名空间；
  - 未匹配路径 → `route.fallback()` 交回**既有 SSRF 策略链**放行真实请求
    （禁止用 continue_ —— 那会跳过先注册的 SSRF handler，形成绕过）；
  - 仅 Web 执行链注入；不引用 Android/Appium 域（不变量 #3）。

匹配语义：path_pattern 以 "/" 分段，段为字面量或 ":name" 占位（匹配任意非空单段）；
请求路径先剥离 server.base_path 前缀再比对。
"""

import asyncio
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger("autopilot.mock")

DEFAULT_BASE_PATH = "/mock"
VALID_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})

#: 占位段名（":name"）
_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ═══════════════════════════════════════════════
# 路径模板校验
# ═══════════════════════════════════════════════

def validate_path_pattern(pattern: Any) -> None:
    """校验路径模板；非法抛 ValueError（供 Pydantic 校验层 → 422）"""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f"路径模板不能为空: {pattern!r}")
    if not pattern.startswith("/"):
        raise ValueError(f"路径模板必须以 / 开头: {pattern!r}")
    if any(c.isspace() for c in pattern):
        raise ValueError(f"路径模板不得含空白字符: {pattern!r}")
    if "?" in pattern or "#" in pattern:
        raise ValueError(f"路径模板不得含查询串/片段: {pattern!r}")
    for seg in [s for s in pattern.split("/") if s != ""]:
        if seg.startswith(":"):
            if not _PARAM_NAME_RE.match(seg[1:]):
                raise ValueError(f"占位段名非法（需 [A-Za-z_][A-Za-z0-9_]*）: {seg!r}")
        elif ":" in seg:
            raise ValueError(f"冒号只能出现在段首作为占位符: {seg!r}")


def is_valid_path_pattern(pattern: Any) -> bool:
    try:
        validate_path_pattern(pattern)
        return True
    except ValueError:
        return False


# ═══════════════════════════════════════════════
# 路径归一化 / 匹配
# ═══════════════════════════════════════════════

def normalize_base_path(base_path: Any) -> str:
    bp = (base_path or DEFAULT_BASE_PATH)
    bp = str(bp).strip()
    if not bp.startswith("/"):
        bp = "/" + bp
    return bp.rstrip("/") or "/"


def url_to_path(url: Any) -> str:
    """从请求 URL 取 path（兼容绝对 URL 与纯路径）"""
    if not url:
        return "/"
    parsed = urlsplit(str(url))
    if parsed.scheme or parsed.netloc:
        return parsed.path or "/"
    return str(url).split("?", 1)[0].split("#", 1)[0] or "/"


def strip_base_path(path: Any, base_path: Any) -> str:
    """剥离 base_path 前缀（大小写敏感，段边界严格）"""
    p = (str(path) if path else "/").split("?", 1)[0].split("#", 1)[0] or "/"
    bp = normalize_base_path(base_path)
    if bp != "/" and (p == bp or p.startswith(bp + "/")):
        p = p[len(bp):] or "/"
    return p or "/"


def _segments(path: Any) -> list[str]:
    return [s for s in (str(path) if path else "").split("/") if s != ""]


def match_path(pattern: str, path: str) -> bool:
    """段级匹配：字面量逐段相等；":name" 匹配任意非空单段"""
    ps = _segments(pattern)
    qs = _segments(path)
    if len(ps) != len(qs):
        return False
    for a, b in zip(ps, qs):
        if a.startswith(":"):
            if not b:
                return False
        elif a != b:
            return False
    return True


def build_route_glob(base_path: Any) -> str:
    """Playwright route glob：仅拦截 base_path 前缀（含绝对 URL 形态）"""
    return f"**{normalize_base_path(base_path)}/**"


def select_rule(rules: list, method: Any, path: str) -> Optional[Any]:
    """首个 enabled 且 method + path 命中的规则（规则按 id 升序）"""
    m = (str(method) if method else "").upper()
    for r in rules or []:
        if not getattr(r, "enabled", True):
            continue
        if str(getattr(r, "method", "")).upper() != m:
            continue
        if match_path(getattr(r, "path_pattern", ""), path):
            return r
    return None


def _loads(v: Any, default: Any) -> Any:
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════
# 拦截 handler
# ═══════════════════════════════════════════════

def build_mock_handler(server: Any, rules: list):
    """构造 Playwright route handler（route, request）→ "fulfilled" / "passed"

    命中：延迟 delay_ms → fulfill(status/headers/body)。
    未命中：route.fallback() 交回既有策略链（不阻断真实请求，也不绕过 SSRF）。
    """

    async def _handler(route, request) -> str:
        method = getattr(request, "method", "") or ""
        url = getattr(request, "url", "") or ""
        relative = strip_base_path(url_to_path(url), getattr(server, "base_path", DEFAULT_BASE_PATH))
        rule = select_rule(rules, method, relative)
        if rule is None:
            logger.warning("Mock 未匹配，放行真实请求: %s %s", method, url)
            await route.fallback()
            return "passed"

        delay_ms = getattr(rule, "delay_ms", 0) or 0
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        headers = _loads(getattr(rule, "response_headers", None), None) or {}
        body = getattr(rule, "response_body", None)
        if body is None:
            body = ""
        await route.fulfill(
            status=getattr(rule, "status_code", 200) or 200,
            headers=headers,
            body=body,
        )
        return "fulfilled"

    return _handler


async def install_mock_for_execution(context, db, server_id: Any) -> int:
    """按 server_id 注册 route 拦截；返回注册数（0 = 未注册）

    仅 enabled 的 server 生效（§4 enabled⇄disabled）；规则执行期读库
    （§4：执行期拦截是 runtime 行为，非持久状态）。
    """
    from app.models.mock import MockRule, MockServer

    if not server_id:
        return 0
    server = db.query(MockServer).filter(MockServer.id == server_id).first()
    if server is None:
        logger.warning("Mock server 不存在，跳过拦截: %s", server_id)
        return 0
    if not server.enabled:
        logger.info("Mock server 已停用，跳过拦截: %s", server_id)
        return 0

    rules = (
        db.query(MockRule)
        .filter(MockRule.server_id == server_id)
        .order_by(MockRule.id.asc())
        .all()
    )
    glob = build_route_glob(server.base_path)
    await context.route(glob, build_mock_handler(server, rules))
    logger.info("Mock 拦截已注册: server=%s glob=%s rules=%s", server_id, glob, len(rules))
    return 1


# ═══════════════════════════════════════════════
# 服务（CRUD 辅助 + dry-run）
# ═══════════════════════════════════════════════

class MockService:
    """规则加载 / 匹配 / dry-run（无副作用校验）"""

    def __init__(self, db: Any) -> None:
        self._db = db

    def load_server(self, server_id: int):
        from app.models.mock import MockServer
        return self._db.query(MockServer).filter(MockServer.id == server_id).first()

    def load_rules(self, server_id: int) -> list:
        from app.models.mock import MockRule
        return (
            self._db.query(MockRule)
            .filter(MockRule.server_id == server_id)
            .order_by(MockRule.id.asc())
            .all()
        )

    def dry_run(self, server_id: int, method: str, path: str) -> dict:
        """dry-run 匹配校验：不执行、不写库、不产生副作用"""
        server = self.load_server(server_id)
        if server is None:
            return {"matched": False, "server_id": server_id, "reason": "server 不存在"}
        rules = self.load_rules(server_id)
        relative = strip_base_path(path, server.base_path)
        rule = select_rule(rules, method, relative)
        if rule is None:
            return {
                "matched": False,
                "server_id": server_id,
                "normalized_path": relative,
                "reason": "无匹配规则（执行期将放行真实请求）",
            }
        return {
            "matched": True,
            "server_id": server_id,
            "normalized_path": relative,
            "rule_id": rule.id,
            "status_code": rule.status_code,
            "delay_ms": rule.delay_ms,
        }
