"""P1-2 验收：selector 唯一性检查 Playwright-native — :has-text() 真正参与唯一性判断

对应验收2：
- :has-text() 唯一性判断不再永远失败
- 同文案两按钮 → count=2 → 正确降级（落到层级兜底）

禁止事项校验：
- 禁止用 document.querySelectorAll 验证 Playwright 专属语法（:has-text 等）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import element_extractor
from app.services.element_extractor import generate_selector, is_unique


def _make_page(counts):
    """counts: list，每次 page.locator().count() 依次返回"""
    page = AsyncMock()
    counts = list(counts)

    def _locator(_sel):
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=counts.pop(0))
        return loc

    page.locator = MagicMock(side_effect=_locator)
    return page


class TestIsUniqueWithHasText:
    """验收2a：:has-text() 经 page.locator().count() 参与唯一性判断"""

    @pytest.mark.asyncio
    async def test_has_text_unique_returns_true(self):
        """同一文案仅一个元素 → count==1 → 唯一（:has-text 不再永远失败）"""
        page = _make_page([1])
        assert await is_unique(page, 'button:has-text("Login")') is True

    @pytest.mark.asyncio
    async def test_has_text_multiple_returns_false(self):
        """同文案两个按钮 → count==2 → 不唯一"""
        page = _make_page([2])
        assert await is_unique(page, 'button:has-text("Login")') is False

    @pytest.mark.asyncio
    async def test_has_text_zero_returns_false(self):
        """文案不存在 → count==0 → 不唯一"""
        page = _make_page([0])
        assert await is_unique(page, 'span:has-text("Nope")') is False

    @pytest.mark.asyncio
    async def test_locator_error_returns_false(self):
        """locator 解析异常 → False（不抛异常）"""
        page = AsyncMock()
        page.locator = MagicMock(side_effect=Exception("bad selector"))
        assert await is_unique(page, 'span:has-text("x")') is False


class TestHasTextDegradation:
    """验收2b：同文案两按钮 → text 级 count=2 → 正确降级到层级兜底"""

    @pytest.mark.asyncio
    async def test_same_text_two_buttons_degrades_to_hierarchy(self):
        """button:has-text("Login") count=2 → 不唯一 → 层级 nth-of-type 路径"""
        # 清空线程本地实例的残留身份上下文（模块级接口持有线程单例，测试间需隔离）
        element_extractor._extractor()._identities.clear()

        page = AsyncMock()
        counts = [2, 1]  # level6 text: 2（不唯一）; 层级 depth1: 1（唯一）

        def _locator(_sel):
            loc = AsyncMock()
            loc.count = AsyncMock(return_value=counts.pop(0))
            return loc

        page.locator = MagicMock(side_effect=_locator)
        identity = AsyncMock()
        identity.evaluate = AsyncMock(return_value=[
            {"tag": "html", "idx": 1}, {"tag": "body", "idx": 1},
            {"tag": "form", "idx": 1}, {"tag": "button", "idx": 1},
        ])
        page.evaluate_handle = AsyncMock(return_value=identity)
        page.evaluate = AsyncMock(return_value=True)  # identity comparison 通过

        raw = {
            "tag": "button", "id": "", "name": "", "placeholder": "",
            "textContent": "Login", "className": "", "dataTestid": "",
            "index": 0,
        }
        sel = await generate_selector(page, raw)
        assert "has-text" not in sel  # 同文案不唯一 → 不再选中
        assert "nth-of-type" in sel
        assert "nth(" not in sel

    @pytest.mark.asyncio
    async def test_has_text_unique_selector_accepted(self):
        """同一文案仅一个元素 → text 级直接命中 :has-text"""
        page = _make_page([1])
        raw = {
            "tag": "span", "id": "", "name": "", "placeholder": "",
            "textContent": "Hello World", "className": "", "dataTestid": "",
            "index": 0,
        }
        sel = await generate_selector(page, raw)
        assert sel == 'span:has-text("Hello World")'
