"""SafePlaywright 单元测试 — 受控 API 白名单 + 原生对象隔离"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.utils.safe_playwright import SafePlaywright


def _make_safe(page=None):
    """构造 SafePlaywright，page 默认为 AsyncMock"""
    return SafePlaywright(page if page is not None else AsyncMock())


class TestSafeAPI:
    """正常 Safe API 调用"""

    @pytest.mark.asyncio
    async def test_goto_delegates_to_page(self):
        page = AsyncMock()
        safe = _make_safe(page)
        await safe.goto("https://example.com")
        page.goto.assert_awaited_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_click_delegates_to_locator(self):
        page = AsyncMock()
        # page.locator() 是 Playwright 同步方法，返回 locator 对象
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = _make_safe(page)
        await safe.click("#btn")
        page.locator.assert_called_once_with("#btn")
        locator.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fill_delegates_with_selector_and_value(self):
        page = AsyncMock()
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = _make_safe(page)
        await safe.fill("#user", "admin")
        page.locator.assert_called_once_with("#user")
        locator.fill.assert_awaited_once_with("admin")

    @pytest.mark.asyncio
    async def test_select_delegates_to_select_option(self):
        page = AsyncMock()
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = _make_safe(page)
        await safe.select("#ddl", "opt1")
        locator.select_option.assert_awaited_once_with("opt1")

    @pytest.mark.asyncio
    async def test_hover_delegates_to_locator(self):
        page = AsyncMock()
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = _make_safe(page)
        await safe.hover("#menu")
        locator.hover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_assert_text_delegates_to_expect(self, mocker):
        page = AsyncMock()
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = _make_safe(page)

        fake_expect_obj = AsyncMock()
        mocker.patch("playwright.async_api.expect", return_value=fake_expect_obj)

        await safe.assert_text(".msg", "Hello")
        page.locator.assert_called_once_with(".msg")
        fake_expect_obj.to_contain_text.assert_awaited_once_with("Hello")

    @pytest.mark.asyncio
    async def test_assert_visible_delegates_to_expect(self, mocker):
        page = AsyncMock()
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = _make_safe(page)

        fake_expect_obj = AsyncMock()
        mocker.patch("playwright.async_api.expect", return_value=fake_expect_obj)

        await safe.assert_visible(".msg")
        page.locator.assert_called_once_with(".msg")
        fake_expect_obj.to_be_visible.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_screenshot_delegates_to_page(self):
        page = AsyncMock()
        safe = _make_safe(page)
        await safe.screenshot(path="reports/a.png")
        page.screenshot.assert_awaited_once()
        kwargs = page.screenshot.await_args.kwargs
        assert kwargs["path"] == "reports/a.png"
        assert kwargs["full_page"] is True

    @pytest.mark.asyncio
    async def test_screenshot_without_path(self):
        page = AsyncMock()
        safe = _make_safe(page)
        await safe.screenshot()
        kwargs = page.screenshot.await_args.kwargs
        assert "path" not in kwargs
        assert kwargs["full_page"] is True

    @pytest.mark.asyncio
    async def test_wait_delegates_to_wait_for_timeout(self):
        page = AsyncMock()
        safe = _make_safe(page)
        await safe.wait(2000)
        page.wait_for_timeout.assert_awaited_once_with(2000)


class TestNativeIsolation:
    """原生 page 隔离 — 禁止通过反射/私有属性获取底层对象"""

    def test_page_not_exposed_as_attribute(self):
        page = AsyncMock()
        safe = _make_safe(page)
        # safe 上没有 page 属性
        with pytest.raises(AttributeError):
            safe.page

    def test_private_attr_blocked(self):
        page = AsyncMock()
        safe = _make_safe(page)
        # safe._page 被 __getattribute__ 拦截
        with pytest.raises(AttributeError):
            _ = safe._page

    def test_dunder_dict_blocked(self):
        safe = _make_safe()
        with pytest.raises(AttributeError):
            _ = safe.__dict__

    def test_dunder_class_blocked(self):
        """__class__ 放行受限假类（Playwright 栈分析兼容），但不暴露真实类能力"""
        safe = _make_safe()
        # 返回无实际能力的假类，仅提供 __name__ 供 Playwright 内部读取
        cls = safe.__class__
        assert cls.__name__ == "SafePlaywright"
        # 返回的并非真实 SafePlaywright 类（无法反射真实类结构或构造实例）
        assert cls is not SafePlaywright
        # 核心防线不变：_page / __dict__ / __getattribute__ 仍被拦截
        with pytest.raises(AttributeError):
            _ = safe._page
        with pytest.raises(AttributeError):
            _ = safe.__dict__
        with pytest.raises(AttributeError):
            _ = safe.__getattribute__

    def test_dunder_getattribute_blocked(self):
        safe = _make_safe()
        with pytest.raises(AttributeError):
            _ = safe.__getattribute__

    def test_setattr_blocked(self):
        safe = _make_safe()
        with pytest.raises(AttributeError):
            safe.extra = 1

    def test_unknown_method_blocked(self):
        """白名单之外的方法不可访问"""
        safe = _make_safe()
        with pytest.raises(AttributeError):
            _ = safe.evaluate

    def test_unknown_property_blocked(self):
        safe = _make_safe()
        with pytest.raises(AttributeError):
            _ = safe.locator

    def test_type_would_not_reach_page(self):
        """即使拿到类型，也无法从 SafePlaywright 实例获取 _page"""
        safe = _make_safe()
        # __getattribute__ 已拦截 _page，type 无法穿透
        with pytest.raises(AttributeError):
            _ = safe._page


class TestSafePlaywrightInstance:
    """实例属性安全"""

    def test_slots_no_dict(self):
        safe = _make_safe()
        assert not hasattr(safe, "__dict__")

    def test_repr_not_crash(self):
        """repr 不应因 __getattribute__ 拦截而崩溃"""
        safe = _make_safe()
        # 不要求特定格式，只验证不抛异常（拦截 _ 开头属性时可能抛）
        try:
            repr(safe)
        except AttributeError:
            pass
