"""AC-01：路径模板校验 + 匹配引擎（单元面）— RED 先行

Spec §6 / §11 AC-01：规则 CRUD + 路径模板校验（非法模式 422）。
匹配语义：pattern 以 "/" 分段，段为字面量或 ":name" 占位（匹配任意非空单段）；
路径先剥离 base_path 前缀再比对。
"""

import pytest

from app.services.mock_service import (
    is_valid_path_pattern,
    validate_path_pattern,
    match_path,
    strip_base_path,
    normalize_base_path,
    build_route_glob,
    select_rule,
)

BAD_PATTERNS = [
    ("", "空模式"),
    ("api/users", "不以 / 开头"),
    ("/api/ :id", "含空白"),
    ("/api/users?x=1", "含查询串"),
    ("/api/users#frag", "含片段"),
    ("/api/:/users", "空占位名"),
    ("/api/a:b", "段内冒号位置非法"),
]

GOOD_PATTERNS = [
    "/api/users/:id",
    "/health",
    "/a/:id/b/:name",
    "/v1/orders/:orderId/items",
]


class TestPathPatternValidation:
    @pytest.mark.parametrize("pattern,reason", BAD_PATTERNS)
    def test_is_valid_rejects_bad(self, pattern, reason):
        assert is_valid_path_pattern(pattern) is False, f"{reason}: {pattern!r}"

    @pytest.mark.parametrize("pattern,reason", BAD_PATTERNS)
    def test_validate_raises(self, pattern, reason):
        with pytest.raises(ValueError):
            validate_path_pattern(pattern)

    @pytest.mark.parametrize("pattern", GOOD_PATTERNS)
    def test_good_pattern_accepted(self, pattern):
        assert is_valid_path_pattern(pattern) is True
        validate_path_pattern(pattern)


class TestMatching:
    @pytest.mark.parametrize("pattern,path,expected", [
        ("/api/users/:id", "/api/users/123", True),
        ("/api/users/:id", "/api/users/", False),
        ("/api/users/:id", "/api/users/1/extra", False),
        ("/api/users/:id", "/api/users", False),
        ("/health", "/health", True),
        ("/health", "/healthz", False),
        ("/a/:id/b/:name", "/a/1/b/x", True),
        ("/a/:id/b/:name", "/a/1/c/x", False),
        ("/", "/", True),
    ])
    def test_match_path(self, pattern, path, expected):
        assert match_path(pattern, path) is expected

    def test_normalize_and_strip_base_path(self):
        assert normalize_base_path("mock") == "/mock"
        assert normalize_base_path("/mock/") == "/mock"
        assert strip_base_path("/mock/api/users/1", "/mock") == "/api/users/1"
        assert strip_base_path("/mock", "/mock") == "/"
        assert strip_base_path("/other/api", "/mock") == "/other/api"

    def test_build_route_glob(self):
        assert build_route_glob("/mock") == "**/mock/**"


class TestSelectRule:
    class _R:
        def __init__(self, method, pattern, enabled=True, tag=None):
            self.method = method
            self.path_pattern = pattern
            self.enabled = enabled
            self.tag = tag

    def test_first_enabled_match_wins(self):
        rules = [
            self._R("GET", "/api/users/:id", tag="a"),
            self._R("GET", "/api/users/:id", tag="b"),
        ]
        got = select_rule(rules, "GET", "/api/users/9")
        assert got is not None and got.tag == "a"

    def test_method_mismatch_skipped(self):
        rules = [self._R("POST", "/api/users/:id")]
        assert select_rule(rules, "GET", "/api/users/9") is None

    def test_disabled_skipped(self):
        rules = [self._R("GET", "/api/users/:id", enabled=False)]
        assert select_rule(rules, "GET", "/api/users/9") is None
