"""元素服务单元测试 — ElementService 全部方法 + 工具函数"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.element_service import (
    ElementService,
    CrawledElement,
    CrawlResult,
    PaginatedResult,
    _css_escape,
    _filter_stable_classes,
    _orm_to_crawled,
)
from app.models.element import PageElement
from app.models.project import Project
from app.exceptions import NotFoundException, PlaywrightException, ValidationException


# ═══════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════

def _make_mock_page(evaluate_side_effect=None):
    """创建一个可配置 evaluate 的 mock page"""
    page = AsyncMock()
    if evaluate_side_effect is not None:
        page.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    else:
        page.evaluate = AsyncMock(return_value=1)
    return page


# ═══════════════════════════════════════════════
# crawl() 测试
# ═══════════════════════════════════════════════

class TestCrawl:
    @pytest.mark.asyncio
    async def test_project_not_found_raises_not_found(self, db_session):
        """场景1: 项目不存在 → NotFoundException"""
        service = ElementService(db_session)
        with pytest.raises(NotFoundException, match="项目 99999 不存在"):
            await service.crawl(99999)

    @pytest.mark.asyncio
    async def test_crawl_android_project_rejected(self, db_session):
        """platform=android 项目调用 Web 抓取 → ValidationException"""
        from app.models.project import Project
        project = Project(
            name="Android Project",
            target_url="https://example.com",
            platform="android",
            status="active",
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        service = ElementService(db_session)
        with pytest.raises(ValidationException, match="仅支持 platform=web"):
            await service.crawl(project.id)

    @pytest.mark.asyncio
    async def test_crawl_success_saves_elements_to_db(
        self, db_session, sample_project, mock_playwright_for_element_service
    ):
        """场景2: 正常抓取 → 返回 CrawlResult，元素存入数据库"""
        mock_page = mock_playwright_for_element_service
        mock_page.evaluate.return_value = [
            {
                "tag": "button", "element_type": "button", "id": "submit-btn",
                "name": None, "className": "btn primary", "textContent": "Submit",
                "placeholder": None, "isVisible": True,
                "boundingBox": {"x": 10, "y": 20, "width": 100, "height": 40},
                "attributes": {"type": "submit"}, "dataTestid": None, "index": 0,
            }
        ]

        service = ElementService(db_session)
        service._generate_selector = AsyncMock(return_value="#submit-btn")

        result = await service.crawl(sample_project.id)

        assert isinstance(result, CrawlResult)
        assert result.url == "https://example.com"
        assert result.crawled_count == 1
        assert result.elapsed_ms >= 0
        assert result.error is None
        assert len(result.elements) == 1
        assert result.elements[0].selector == "#submit-btn"

        # 验证数据库
        elements = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == sample_project.id)
            .all()
        )
        assert len(elements) == 1
        assert elements[0].selector == "#submit-btn"
        assert elements[0].element_type == "button"

    @pytest.mark.asyncio
    async def test_crawl_clears_old_data_before_insert(
        self, db_session, sample_project, mock_playwright_for_element_service
    ):
        """场景3: 抓取前清空旧数据"""
        old_el = PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#old-btn", text_content="Old", is_visible=1,
        )
        db_session.add(old_el)
        db_session.commit()

        mock_page = mock_playwright_for_element_service
        mock_page.evaluate.return_value = [
            {
                "tag": "input", "element_type": "text", "id": "new-input",
                "name": "username", "className": "", "textContent": "",
                "placeholder": "Enter name", "isVisible": True,
                "boundingBox": {"x": 0, "y": 0, "width": 200, "height": 30},
                "attributes": {}, "dataTestid": None, "index": 0,
            }
        ]

        service = ElementService(db_session)
        service._generate_selector = AsyncMock(return_value='[name="username"]')

        await service.crawl(sample_project.id)

        elements = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == sample_project.id)
            .all()
        )
        assert len(elements) == 1
        assert elements[0].selector == '[name="username"]'

    @pytest.mark.asyncio
    async def test_crawl_empty_page_returns_zero_elements(
        self, db_session, sample_project, mock_playwright_for_element_service
    ):
        """场景4: 空页面 → 返回 0 个元素"""
        mock_page = mock_playwright_for_element_service
        mock_page.evaluate.return_value = []

        service = ElementService(db_session)
        result = await service.crawl(sample_project.id)

        assert result.crawled_count == 0
        assert result.elements == []

        elements = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == sample_project.id)
            .all()
        )
        assert len(elements) == 0

    @pytest.mark.asyncio
    async def test_crawl_page_goto_failure_raises_playwright_exception(
        self, db_session, sample_project, mock_playwright_for_element_service
    ):
        """场景5: 页面访问失败 → PlaywrightException"""
        mock_page = mock_playwright_for_element_service
        mock_page.goto = AsyncMock(side_effect=Exception("Connection refused"))

        service = ElementService(db_session)
        with pytest.raises(PlaywrightException, match="无法访问页面"):
            await service.crawl(sample_project.id)

    @pytest.mark.asyncio
    async def test_crawl_evaluate_failure_raises_playwright_exception(
        self, db_session, sample_project, mock_playwright_for_element_service
    ):
        """场景6: JS 执行失败 → PlaywrightException"""
        mock_page = mock_playwright_for_element_service
        mock_page.evaluate = AsyncMock(side_effect=Exception("JS error"))

        service = ElementService(db_session)
        with pytest.raises(PlaywrightException, match="元素提取脚本执行失败"):
            await service.crawl(sample_project.id)

    @pytest.mark.asyncio
    async def test_crawl_browser_launch_failure(
        self, db_session, sample_project, mock_playwright_for_element_service
    ):
        """场景7: 浏览器启动失败 → PlaywrightException"""
        mock_page = mock_playwright_for_element_service
        # 需要让 browser.launch 抛出异常
        # 通过 mock_playwright_for_element_service 的 mock_pw 链来控制
        # 直接修改 mock_page 不够，需要修改 launch 的返回值
        # 复用 fixture 但重新 patch launch
        pass  # 见下方单独测试

    @pytest.mark.asyncio
    async def test_crawl_url_construction_with_test_path(
        self, db_session, mock_playwright_for_element_service
    ):
        """场景8: URL 拼接 — target_url + test_path"""
        project = Project(
            name="Path Test", target_url="https://example.com/app/",
            test_path="/login/page", browser_type="chromium", headless=1,
        )
        db_session.add(project)
        db_session.commit()

        mock_page = mock_playwright_for_element_service
        mock_page.evaluate.return_value = []

        service = ElementService(db_session)
        result = await service.crawl(project.id)

        assert result.url == "https://example.com/app/login/page"


# ═══════════════════════════════════════════════
# _extract_elements() 测试
# ═══════════════════════════════════════════════

class TestExtractElements:
    @pytest.mark.asyncio
    async def test_extract_multiple_elements(
        self, db_session, mock_playwright_for_element_service
    ):
        """场景9: 提取多个元素 → 返回 CrawledElement 列表"""
        mock_page = mock_playwright_for_element_service
        mock_page.evaluate.return_value = [
            {
                "tag": "button", "element_type": "button", "id": "btn1",
                "name": None, "className": "", "textContent": "Click Me",
                "placeholder": None, "isVisible": True,
                "boundingBox": {"x": 0, "y": 0, "width": 100, "height": 40},
                "attributes": {}, "dataTestid": None, "index": 0,
            },
            {
                "tag": "input", "element_type": "text", "id": "",
                "name": "email", "className": "form-input",
                "textContent": "", "placeholder": "Enter email",
                "isVisible": True,
                "boundingBox": {"x": 0, "y": 50, "width": 200, "height": 30},
                "attributes": {}, "dataTestid": None, "index": 1,
            },
        ]

        service = ElementService(db_session)
        service._is_unique = AsyncMock(return_value=True)

        elements = await service._extract_elements(
            "https://example.com", "chromium"
        )

        assert len(elements) == 2
        assert elements[0].tag_name == "button"
        assert elements[0].element_type == "button"
        assert elements[0].text_content == "Click Me"
        assert elements[1].tag_name == "input"
        assert elements[1].placeholder == "Enter email"

    @pytest.mark.asyncio
    async def test_extract_empty_page_returns_empty_list(
        self, db_session, mock_playwright_for_element_service
    ):
        """场景10: 空页面 → 返回空列表"""
        mock_page = mock_playwright_for_element_service
        mock_page.evaluate.return_value = []

        service = ElementService(db_session)
        elements = await service._extract_elements(
            "https://example.com", "chromium"
        )
        assert elements == []

    @pytest.mark.asyncio
    async def test_extract_playwright_not_installed(self, db_session, mocker):
        """场景11: Playwright 未安装 → PlaywrightException"""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "playwright.async_api":
                raise ImportError("No module named 'playwright'")
            return real_import(name, globals, locals, fromlist, level)

        mocker.patch("builtins.__import__", side_effect=mock_import)

        service = ElementService(db_session)
        with pytest.raises(PlaywrightException, match="Playwright 未安装"):
            await service._extract_elements("https://example.com", "chromium")

    @pytest.mark.asyncio
    async def test_extract_browser_launch_failure(self, db_session, mocker):
        """场景12: 浏览器启动失败 → PlaywrightException"""
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(
            side_effect=Exception("Browser not found")
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mocker.patch(
            "playwright.async_api.async_playwright",
            return_value=mock_ctx,
        )

        service = ElementService(db_session)
        with pytest.raises(PlaywrightException, match="浏览器启动失败"):
            await service._extract_elements("https://example.com", "chromium")

    @pytest.mark.asyncio
    async def test_extract_page_goto_failure(self, db_session, mocker):
        """场景13: 页面导航失败 → PlaywrightException"""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(
            side_effect=Exception("net::ERR_CONNECTION_REFUSED")
        )

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mocker.patch(
            "playwright.async_api.async_playwright",
            return_value=mock_ctx,
        )

        service = ElementService(db_session)
        with pytest.raises(PlaywrightException, match="无法访问页面"):
            await service._extract_elements("https://example.com", "chromium")

    @pytest.mark.asyncio
    async def test_extract_evaluate_failure(self, db_session, mocker):
        """场景14: 元素提取脚本执行失败 → PlaywrightException"""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Script error"))

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mocker.patch(
            "playwright.async_api.async_playwright",
            return_value=mock_ctx,
        )

        service = ElementService(db_session)
        with pytest.raises(PlaywrightException, match="元素提取脚本执行失败"):
            await service._extract_elements("https://example.com", "chromium")


# ═══════════════════════════════════════════════
# _ai_assisted_navigation() 测试（AI 感知页面抓取）
# ═══════════════════════════════════════════════

class TestAiAssistedNavigation:
    """_ai_assisted_navigation() — goto 失败时 AI 分析截图并执行前置操作"""

    @pytest.mark.asyncio
    async def test_vision_unavailable_returns_false(self, db_session, mocker):
        """无 API Key / Vision 返回空 → 返回 False（跳过 AI 导航）"""
        mocker.patch(
            "app.services.element_service._call_openai_vision", return_value=""
        )
        page = AsyncMock()
        page.screenshot.return_value = b"fake_image"

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_need_pre_actions_executes_click_and_fill(self, db_session, mocker):
        """AI 判断需要前置操作 → 执行 click/fill → 返回 True"""
        mocker.patch(
            "app.services.element_service._call_openai_vision",
            return_value=json.dumps({
                "need_pre_actions": True,
                "actions": [
                    {"action": "click", "selector": "text=登录"},
                    {"action": "fill", "selector": "#username", "value": "admin"},
                ],
                "reason": "需要登录后访问",
            }),
        )
        page = AsyncMock()
        page.screenshot.return_value = b"fake_image"

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")

        assert result is True
        page.click.assert_awaited_once_with("text=登录")
        page.fill.assert_awaited_once_with("#username", "admin")

    @pytest.mark.asyncio
    async def test_no_pre_actions_returns_false(self, db_session, mocker):
        """AI 判断无需前置操作 → 返回 False"""
        mocker.patch(
            "app.services.element_service._call_openai_vision",
            return_value=json.dumps({
                "need_pre_actions": False,
                "actions": [],
                "reason": "页面已正常加载",
            }),
        )
        page = AsyncMock()
        page.screenshot.return_value = b"fake_image"

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")
        assert result is False
        page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_false(self, db_session, mocker):
        """AI 返回非法 JSON → 解析失败 → 返回 False"""
        mocker.patch(
            "app.services.element_service._call_openai_vision",
            return_value="这不是 JSON",
        )
        page = AsyncMock()
        page.screenshot.return_value = b"fake_image"

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json_parsed(self, db_session, mocker):
        """AI 返回 markdown 代码块包裹的 JSON → 正确解析并执行"""
        mocker.patch(
            "app.services.element_service._call_openai_vision",
            return_value='```json\n{"need_pre_actions": true, "actions": [{"action": "click", "selector": "text=确认"}], "reason": "弹窗确认"}\n```',
        )
        page = AsyncMock()
        page.screenshot.return_value = b"fake_image"

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")
        assert result is True
        page.click.assert_awaited_once_with("text=确认")

    @pytest.mark.asyncio
    async def test_screenshot_failure_returns_false(self, db_session, mocker):
        """截图失败 → 返回 False（不阻塞后续）"""
        mocker.patch(
            "app.services.element_service._call_openai_vision", return_value=""
        )
        page = AsyncMock()
        page.screenshot.side_effect = Exception("screenshot error")

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_operation_failure_does_not_raise(self, db_session, mocker):
        """单个操作失败不中断 → 仍返回 True（执行了部分操作）"""
        mocker.patch(
            "app.services.element_service._call_openai_vision",
            return_value=json.dumps({
                "need_pre_actions": True,
                "actions": [
                    {"action": "click", "selector": "text=登录"},
                    {"action": "fill", "selector": "#username", "value": "admin"},
                ],
                "reason": "需要登录",
            }),
        )
        page = AsyncMock()
        page.screenshot.return_value = b"fake_image"
        page.click.side_effect = Exception("element not found")

        service = ElementService(db_session)
        result = await service._ai_assisted_navigation(page, "https://example.com")
        assert result is True


# ═══════════════════════════════════════════════
# _generate_selector() 测试（7 级优先级）
# ═══════════════════════════════════════════════

class TestGenerateSelector:
    @pytest.mark.asyncio
    async def test_level_1_data_testid(self, db_session):
        """第1级: data-testid 属性"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "button", "dataTestid": "submit-btn", "id": "btn1",
            "name": "submit", "placeholder": "", "textContent": "Submit",
            "className": "", "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == '[data-testid="submit-btn"]'

    @pytest.mark.asyncio
    async def test_level_2_id(self, db_session):
        """第2级: id 属性"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "button", "dataTestid": "", "id": "myButton",
            "name": "", "placeholder": "", "textContent": "Click",
            "className": "", "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == "#myButton"

    @pytest.mark.asyncio
    async def test_level_3_name(self, db_session):
        """第3级: name 属性"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "input", "dataTestid": "", "id": "", "name": "username",
            "placeholder": "", "textContent": "", "className": "", "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == '[name="username"]'

    @pytest.mark.asyncio
    async def test_level_4_placeholder_with_tag(self, db_session):
        """第4级: placeholder + tag"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "input", "dataTestid": "", "id": "", "name": "",
            "placeholder": "Enter your email", "textContent": "",
            "className": "", "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == 'input[placeholder="Enter your email"]'

    @pytest.mark.asyncio
    async def test_level_5_stable_class(self, db_session):
        """第5级: 稳定 class（过滤动态类）"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "div", "dataTestid": "", "id": "", "name": "",
            "placeholder": "", "textContent": "", "className": "card container",
            "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == "div.card.container"

    @pytest.mark.asyncio
    async def test_level_6_text_content(self, db_session):
        """第6级: text content 匹配"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "span", "dataTestid": "", "id": "", "name": "",
            "placeholder": "", "textContent": "Hello World", "className": "",
            "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == 'span:has-text("Hello World")'

    @pytest.mark.asyncio
    async def test_level_7_nth_child_fallback(self, db_session):
        """第7级: 兜底 nth-child（所有高级选择器都不唯一）"""
        # 所有唯一性检查都返回 False（count != 1）
        page = _make_mock_page(evaluate_side_effect=[0, 0, 0, 0, 0, 0])
        service = ElementService(db_session)
        raw = {
            "tag": "div", "dataTestid": "", "id": "", "name": "",
            "placeholder": "", "textContent": "", "className": "", "index": 2,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == "div:nth-child(3)"

    @pytest.mark.asyncio
    async def test_falls_through_to_id_when_data_testid_empty(self, db_session):
        """data-testid 为空 → 回退到 id"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "a", "dataTestid": "", "id": "link1", "name": "",
            "placeholder": "", "textContent": "Link", "className": "", "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == "#link1"

    @pytest.mark.asyncio
    async def test_css_escape_applied_to_id(self, db_session):
        """id 含特殊字符 → CSS 转义"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "div", "dataTestid": "", "id": "app:content.main",
            "name": "", "placeholder": "", "textContent": "",
            "className": "", "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == r"#app\:content\.main"

    @pytest.mark.asyncio
    async def test_level_5_filters_dynamic_classes(self, db_session):
        """第5级: 动态 class 被过滤，只保留稳定 class"""
        page = _make_mock_page()
        service = ElementService(db_session)
        raw = {
            "tag": "button", "dataTestid": "", "id": "", "name": "",
            "placeholder": "", "textContent": "",
            "className": "css-1a2b3c4d btn-primary _abc123def",
            "index": 0,
        }

        selector = await service._generate_selector(page, raw)
        assert selector == "button.btn-primary"


# ═══════════════════════════════════════════════
# _is_unique() 测试
# ═══════════════════════════════════════════════

class TestIsUnique:
    @pytest.mark.asyncio
    async def test_unique_selector_returns_true(self, db_session):
        """唯一选择器 → True"""
        page = _make_mock_page(evaluate_side_effect=[1])
        result = await ElementService._is_unique(page, "#my-btn")
        assert result is True

    @pytest.mark.asyncio
    async def test_non_unique_selector_returns_false(self, db_session):
        """多个匹配 → False"""
        page = _make_mock_page(evaluate_side_effect=[3])
        result = await ElementService._is_unique(page, ".btn")
        assert result is False

    @pytest.mark.asyncio
    async def test_zero_elements_returns_false(self, db_session):
        """0 个匹配 → False"""
        page = _make_mock_page(evaluate_side_effect=[0])
        result = await ElementService._is_unique(page, "#nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_evaluate_error_returns_false(self, db_session):
        """evaluate 抛出异常 → False"""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("Invalid selector"))
        result = await ElementService._is_unique(page, "invalid[[")
        assert result is False


# ═══════════════════════════════════════════════
# list_paginated() 测试
# ═══════════════════════════════════════════════

class TestListPaginated:
    def test_empty_project_returns_zero(self, db_session, sample_project):
        """空项目 → total=0, items=[], pages=0"""
        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id)
        assert isinstance(result, PaginatedResult)
        assert result.total == 0
        assert result.items == []
        assert result.pages == 0

    def test_with_multiple_elements(self, db_session, sample_project):
        """多个元素 → 全部返回"""
        for i in range(5):
            db_session.add(PageElement(
                project_id=sample_project.id, element_type="button",
                tag_name="button", selector=f"#btn-{i}",
                text_content=f"Button {i}", is_visible=1,
            ))
        db_session.commit()

        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id)
        assert result.total == 5
        assert len(result.items) == 5

    def test_pagination(self, db_session, sample_project):
        """分页 → 正确计算 pages 和 offset"""
        for i in range(10):
            db_session.add(PageElement(
                project_id=sample_project.id, element_type="input",
                tag_name="input", selector=f"#input-{i}", is_visible=1,
            ))
        db_session.commit()

        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id, page=1, size=3)
        assert result.total == 10
        assert len(result.items) == 3
        assert result.page == 1
        assert result.pages == 4

        result2 = service.list_paginated(sample_project.id, page=4, size=3)
        assert len(result2.items) == 1
        assert result2.page == 4

    def test_filter_by_element_type(self, db_session, sample_project):
        """按 element_type 过滤"""
        db_session.add(PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#btn1", is_visible=1,
        ))
        db_session.add(PageElement(
            project_id=sample_project.id, element_type="input",
            tag_name="input", selector="#input1", is_visible=1,
        ))
        db_session.commit()

        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id, element_type="button")
        assert result.total == 1
        assert result.items[0].element_type == "button"

    def test_filter_by_keyword_matches_text(self, db_session, sample_project):
        """按 keyword 过滤 — 匹配 text_content"""
        db_session.add(PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#login-btn", text_content="Login",
            name="loginButton", is_visible=1,
        ))
        db_session.add(PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#signup-btn", text_content="Sign Up",
            name="signup", is_visible=1,
        ))
        db_session.commit()

        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id, keyword="login")
        assert result.total == 1
        assert result.items[0].text_content == "Login"

    def test_filter_by_keyword_matches_selector(self, db_session, sample_project):
        """按 keyword 过滤 — 匹配 selector"""
        db_session.add(PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#header-btn", is_visible=1,
        ))
        db_session.add(PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#footer-btn", is_visible=1,
        ))
        db_session.commit()

        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id, keyword="header")
        assert result.total == 1
        assert result.items[0].selector == "#header-btn"

    def test_returns_crawled_element_objects(self, db_session, sample_project):
        """返回的 items 是 CrawledElement 对象"""
        el = PageElement(
            project_id=sample_project.id, element_type="link", tag_name="a",
            selector="a.nav-link", text_content="Home", is_visible=1,
            bounding_box=json.dumps({"x": 0, "y": 0, "width": 50, "height": 20}),
            attributes=json.dumps({"href": "/home"}),
        )
        db_session.add(el)
        db_session.commit()

        service = ElementService(db_session)
        result = service.list_paginated(sample_project.id)
        assert result.total == 1
        item = result.items[0]
        assert isinstance(item, CrawledElement)
        assert item.element_type == "link"
        assert item.db_id == el.id
        assert item.bounding_box == {"x": 0, "y": 0, "width": 50, "height": 20}
        assert item.attributes == {"href": "/home"}


# ═══════════════════════════════════════════════
# clear_all() 测试
# ═══════════════════════════════════════════════

class TestClearAll:
    def test_clear_all_removes_all_elements(self, db_session, sample_project):
        """清空项目所有元素 → 返回删除数量"""
        for i in range(3):
            db_session.add(PageElement(
                project_id=sample_project.id, element_type="button",
                tag_name="button", selector=f"#btn-{i}", is_visible=1,
            ))
        db_session.commit()

        service = ElementService(db_session)
        deleted = service.clear_all(sample_project.id)
        assert deleted == 3

        remaining = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == sample_project.id)
            .all()
        )
        assert len(remaining) == 0

    def test_clear_all_empty_project_returns_zero(self, db_session, sample_project):
        """空项目 → 返回 0"""
        service = ElementService(db_session)
        deleted = service.clear_all(sample_project.id)
        assert deleted == 0

    def test_clear_all_only_affects_target_project(self, db_session, sample_project):
        """只清除指定项目，不影响其他项目"""
        project2 = Project(name="Project 2", target_url="https://other.com")
        db_session.add(project2)
        db_session.commit()

        db_session.add(PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#btn1", is_visible=1,
        ))
        db_session.add(PageElement(
            project_id=project2.id, element_type="input",
            tag_name="input", selector="#input2", is_visible=1,
        ))
        db_session.commit()

        service = ElementService(db_session)
        deleted = service.clear_all(sample_project.id)
        assert deleted == 1

        # Project 2 的元素不受影响
        p2_elements = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == project2.id)
            .all()
        )
        assert len(p2_elements) == 1


# ═══════════════════════════════════════════════
# get_element() 测试（通过 DB 查询）
# ═══════════════════════════════════════════════

class TestGetElement:
    def test_get_element_by_id(self, db_session, sample_project):
        """通过 ID 获取单个元素"""
        el = PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#submit-btn", text_content="Submit",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        retrieved = (
            db_session.query(PageElement)
            .filter(PageElement.id == el.id)
            .first()
        )
        assert retrieved is not None
        assert retrieved.selector == "#submit-btn"
        assert retrieved.element_type == "button"

    def test_get_nonexistent_element_returns_none(self, db_session):
        """不存在的元素 → None"""
        result = (
            db_session.query(PageElement)
            .filter(PageElement.id == 99999)
            .first()
        )
        assert result is None

    def test_get_element_by_project_and_selector(self, db_session, sample_project):
        """通过 project_id + selector 获取元素"""
        el = PageElement(
            project_id=sample_project.id, element_type="input",
            tag_name="input", selector="#email", is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        retrieved = (
            db_session.query(PageElement)
            .filter(
                PageElement.project_id == sample_project.id,
                PageElement.selector == "#email",
            )
            .first()
        )
        assert retrieved is not None
        assert retrieved.element_type == "input"


# ═══════════════════════════════════════════════
# 工具函数 _css_escape() 测试
# ═══════════════════════════════════════════════

class TestCssEscape:
    def test_escape_colon(self):
        assert _css_escape("app:content") == "app\\:content"

    def test_escape_dot(self):
        assert _css_escape("my.id") == "my\\.id"

    def test_escape_hash(self):
        assert _css_escape("my#id") == "my\\#id"

    def test_escape_multiple_special_chars(self):
        result = _css_escape("app:content.main#section")
        assert result == "app\\:content\\.main\\#section"

    def test_no_special_chars_returns_unchanged(self):
        assert _css_escape("simpleId") == "simpleId"


# ═══════════════════════════════════════════════
# 工具函数 _filter_stable_classes() 测试
# ═══════════════════════════════════════════════

class TestFilterStableClasses:
    def test_filters_css_in_js_pattern(self):
        """css-xxxxxx 模式被过滤"""
        classes = ["css-1a2b3c4d", "container"]
        result = _filter_stable_classes(classes)
        assert result == ["container"]

    def test_filters_underscore_hash_classes(self):
        """_xxxxxx 模式被过滤"""
        classes = ["_abc123def", "btn-primary"]
        result = _filter_stable_classes(classes)
        assert result == ["btn-primary"]

    def test_filters_hex_suffix_classes(self):
        """xxxx-xxxxxx 模式被过滤"""
        classes = ["text-a1b2c3d", "nav-item"]
        result = _filter_stable_classes(classes)
        assert result == ["nav-item"]

    def test_filters_styled_components(self):
        """sc-xxxxxx 模式被过滤"""
        classes = ["sc-bdVaJa", "header"]
        result = _filter_stable_classes(classes)
        assert result == ["header"]

    def test_filters_long_ant_design_classes(self):
        """ant- 前缀长类名被过滤"""
        classes = ["ant-btn-primary-very-long-suffix-123", "ant-btn"]
        result = _filter_stable_classes(classes)
        assert result == ["ant-btn"]

    def test_keeps_short_ant_design_classes(self):
        """短 ant- 类名保留"""
        classes = ["ant-btn", "ant-input"]
        result = _filter_stable_classes(classes)
        assert result == ["ant-btn", "ant-input"]

    def test_filters_empty_and_single_char_classes(self):
        """空字符串和单字符类名被过滤"""
        classes = ["", "a", "valid-class"]
        result = _filter_stable_classes(classes)
        assert result == ["valid-class"]

    def test_returns_all_stable_classes(self):
        """返回所有稳定类名（不限制数量）"""
        classes = ["btn", "primary", "large"]
        result = _filter_stable_classes(classes)
        assert result == ["btn", "primary", "large"]


# ═══════════════════════════════════════════════
# 工具函数 _orm_to_crawled() 测试
# ═══════════════════════════════════════════════

class TestOrmToCrawled:
    def test_converts_basic_fields(self, db_session, sample_project):
        """基本字段转换"""
        el = PageElement(
            project_id=sample_project.id, element_type="button",
            tag_name="button", selector="#btn", text_content="Click",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        result = _orm_to_crawled(el)
        assert isinstance(result, CrawledElement)
        assert result.element_type == "button"
        assert result.tag_name == "button"
        assert result.element_id is None
        assert result.name is None
        assert result.class_name is None
        assert result.selector == "#btn"
        assert result.text_content == "Click"
        assert result.is_visible == 1
        assert result.db_id == el.id

    def test_converts_with_json_fields(self, db_session, sample_project):
        """JSON 字段正确解析"""
        el = PageElement(
            project_id=sample_project.id, element_type="input",
            tag_name="input", element_id="email", name="email",
            class_name="form-control", selector="#email",
            placeholder="Enter email", is_visible=1,
            bounding_box=json.dumps(
                {"x": 10, "y": 20, "width": 200, "height": 30}
            ),
            attributes=json.dumps({"type": "email", "required": "true"}),
        )
        db_session.add(el)
        db_session.commit()

        result = _orm_to_crawled(el)
        assert result.element_id == "email"
        assert result.name == "email"
        assert result.class_name == "form-control"
        assert result.placeholder == "Enter email"
        assert result.bounding_box == {
            "x": 10, "y": 20, "width": 200, "height": 30,
        }
        assert result.attributes == {"type": "email", "required": "true"}

    def test_converts_none_bounding_box_to_empty_dict(
        self, db_session, sample_project
    ):
        """bounding_box 为 None → {}"""
        el = PageElement(
            project_id=sample_project.id, element_type="div",
            tag_name="div", selector=".container", is_visible=1,
            bounding_box=None,
        )
        db_session.add(el)
        db_session.commit()

        result = _orm_to_crawled(el)
        assert result.bounding_box == {}

    def test_converts_none_attributes_to_empty_dict(
        self, db_session, sample_project
    ):
        """attributes 为 None → {}"""
        el = PageElement(
            project_id=sample_project.id, element_type="div",
            tag_name="div", selector=".container", is_visible=1,
            attributes=None,
        )
        db_session.add(el)
        db_session.commit()

        result = _orm_to_crawled(el)
        assert result.attributes == {}

    def test_converts_is_visible_zero(self, db_session, sample_project):
        """is_visible=0 → 0"""
        el = PageElement(
            project_id=sample_project.id, element_type="div",
            tag_name="div", selector=".hidden", is_visible=0,
        )
        db_session.add(el)
        db_session.commit()

        result = _orm_to_crawled(el)
        assert result.is_visible == 0

    def test_converts_empty_element_id_to_none(self, db_session, sample_project):
        """空字符串 element_id → None"""
        el = PageElement(
            project_id=sample_project.id, element_type="div",
            tag_name="div", selector=".box", element_id="", is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        result = _orm_to_crawled(el)
        assert result.element_id is None