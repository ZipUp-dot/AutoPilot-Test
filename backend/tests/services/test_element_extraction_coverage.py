"""P1-2 验收：元素提取白名单补全 — 白名单覆盖 + 新增类型生成合法 selector

对应验收：
1. saucedemo 登录页抓取 ≥3 个元素（username / password / login button）
5. 白名单新增类型均可被抓取并生成合法 selector

约束校验：
- selector 字段为持久化字符串，禁止把 locator.nth() 运行时 API 链存入
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import element_extractor
from app.services.element_extractor import (
    _SELECTOR_PATTERNS,
    extract_elements,
    generate_selector,
)


# ═══════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════

def _make_extract_page(raw_list, count_value=1):
    """page.evaluate 返回 raw_list；page.locator().count() 返回 count_value"""
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=raw_list)
    locator = AsyncMock()
    locator.count = AsyncMock(return_value=count_value)
    page.locator = MagicMock(return_value=locator)
    page.evaluate_handle = AsyncMock(return_value=AsyncMock())
    return page


def _raw_element(**overrides):
    base = {
        "index": 0, "tag": "input", "element_type": "text",
        "id": "", "name": None, "className": "",
        "textContent": "", "placeholder": None, "type": None,
        "href": None, "role": None, "dataTestid": None,
        "isVisible": True,
        "boundingBox": {"x": 0, "y": 0, "width": 100, "height": 30},
        "attributes": None,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════
# 白名单覆盖
# ═══════════════════════════════════════════════

class TestWhitelistCoverage:
    """提取白名单必须覆盖真实可交互控件（只扩覆盖，不改过滤哲学）"""

    def test_whitelist_contains_form_controls(self):
        """submit/checkbox/radio/file/select/textarea/button 必须入白名单"""
        assert "input[type='submit']" in _SELECTOR_PATTERNS
        assert "input[type='reset']" in _SELECTOR_PATTERNS
        assert "input[type='checkbox']" in _SELECTOR_PATTERNS
        assert "input[type='radio']" in _SELECTOR_PATTERNS
        assert "input[type='file']" in _SELECTOR_PATTERNS
        assert "select" in _SELECTOR_PATTERNS
        assert "textarea" in _SELECTOR_PATTERNS
        assert "button" in _SELECTOR_PATTERNS

    def test_whitelist_contains_other_interactive_controls(self):
        """其他真实可交互控件：文本类/无 type/选择类/contenteditable/链接/role 系列"""
        assert "input:not([type])" in _SELECTOR_PATTERNS
        assert "input[type='text']" in _SELECTOR_PATTERNS
        assert "input[type='password']" in _SELECTOR_PATTERNS
        assert "input[type='search']" in _SELECTOR_PATTERNS
        assert "input[type='datetime-local']" in _SELECTOR_PATTERNS
        assert "[contenteditable='true']" in _SELECTOR_PATTERNS
        assert "a[href]" in _SELECTOR_PATTERNS
        for role_sel in ("[role='button']", "[role='textbox']", "[role='switch']",
                         "[role='slider']", "[role='combobox']"):
            assert role_sel in _SELECTOR_PATTERNS

    def test_extract_js_built_from_single_whitelist(self):
        """提取脚本由白名单 JSON 生成（单一来源，脚本内不遗漏 submit）"""
        assert json.dumps(_SELECTOR_PATTERNS) in element_extractor._EXTRACT_JS
        assert "input[type='submit']" in element_extractor._EXTRACT_JS


# ═══════════════════════════════════════════════
# 验收1：saucedemo 登录页 ≥3 个元素
# ═══════════════════════════════════════════════

class TestSaucedemoLikePage:
    """验收1：登录页 username/password/login button 三种元素全部被抓取"""

    @pytest.mark.asyncio
    async def test_saucedemo_login_page_extracts_3_elements(self):
        """模拟 saucedemo 登录页：用户名输入框 / 密码输入框 / 登录提交按钮"""
        raw_list = [
            _raw_element(index=0, tag="input", element_type="text", id="user-name",
                         className="form_input", placeholder="Username", type="text"),
            _raw_element(index=1, tag="input", element_type="password", id="password",
                         className="form_input", placeholder="Password", type="password"),
            _raw_element(index=2, tag="input", element_type="submit", id="login-button",
                         className="submit-button", textContent="Login", type="submit"),
        ]
        page = _make_extract_page(raw_list)

        elements = await extract_elements(page)
        assert len(elements) == 3

        types = {el["element_type"] for el in elements}
        assert {"text", "password", "submit"} <= types  # username/password/login button

        # 每个元素都能生成合法 selector，且不携带 locator.nth() 运行时链
        for raw in elements:
            sel = await generate_selector(page, raw)
            assert sel
            assert "nth(" not in sel


# ═══════════════════════════════════════════════
# 验收5：白名单新增类型均可被抓取并生成合法 selector
# ═══════════════════════════════════════════════

class TestNewTypesGenerateSelector:
    """验收5：submit/checkbox/radio/file/select/textarea/button 均可被抓取 + 生成合法 selector"""

    NEW_TYPES = [
        _raw_element(tag="input", element_type="submit", type="submit", placeholder="Search"),
        _raw_element(tag="input", element_type="checkbox", type="checkbox", name="agree"),
        _raw_element(tag="input", element_type="radio", type="radio", name="gender"),
        _raw_element(tag="input", element_type="file", type="file", name="avatar"),
        _raw_element(tag="select", element_type="select", name="country"),
        _raw_element(tag="textarea", element_type="textarea", placeholder="Message"),
        _raw_element(tag="button", element_type="button", id="go-btn", textContent="Go"),
    ]

    @pytest.mark.asyncio
    async def test_each_type_is_extracted_and_generates_selector(self):
        for raw in self.NEW_TYPES:
            page = _make_extract_page([raw], count_value=1)
            elements = await extract_elements(page)
            assert len(elements) == 1, f"{raw['element_type']} 应被抓取"

            sel = await generate_selector(page, elements[0])
            assert sel, f"{raw['element_type']} 应生成非空 selector"
            assert "nth(" not in sel, "禁止把 locator.nth() 运行时 API 链存进 selector 字段"

    @pytest.mark.asyncio
    async def test_selector_uses_expected_attribute_level(self):
        """各类型落到对应的首选属性级（id/name/placeholder），而非一律 XPath"""
        cases = [
            (_raw_element(tag="button", element_type="button", id="go-btn"), "#go-btn"),
            (_raw_element(tag="input", element_type="checkbox", type="checkbox", name="agree"),
             '[name="agree"]'),
            (_raw_element(tag="textarea", element_type="textarea", placeholder="Message"),
             'textarea[placeholder="Message"]'),
        ]
        for raw, expected in cases:
            page = _make_extract_page([raw], count_value=1)
            elements = await extract_elements(page)
            sel = await generate_selector(page, elements[0])
            assert sel == expected, f"{raw['element_type']}: {sel}"
