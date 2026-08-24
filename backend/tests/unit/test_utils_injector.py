"""CodeInjector unit tests — AST monitoring hook injection."""

import ast
import pytest

from app.utils.code_injector import CodeInjector, _extract_op_info
from app.exceptions import SecurityException

MOCK_CODE = '''from playwright.async_api import Page, expect
import asyncio
from datetime import datetime

async def run_test(page: Page) -> dict:
    steps_result = []
    start_time = datetime.now()
    try:
        await page.goto("https://example.com")
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path="test.png", full_page=True)
        await page.locator("#username").fill("admin")
        await page.locator("button[type=submit]").click()
        await expect(page.locator(".welcome")).to_contain_text("Welcome")
        steps_result.append({"step": 1, "status": "passed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}
    duration = (datetime.now() - start_time).total_seconds()
    return {"success": True, "message": f"ok {duration}s", "steps": steps_result}
'''


class TestCodeInjectorInject:
    """inject() — main injection method"""

    def test_inject_valid_code_has_monitor_before(self):
        injected = CodeInjector.inject(MOCK_CODE)
        assert "__monitor_before" in injected

    def test_inject_monitors_around_goto(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.goto("https://example.com")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected
        assert "page.goto" in injected

    def test_inject_monitors_around_fill(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.locator("#username").fill("admin")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected
        assert "fill" in injected

    def test_inject_monitors_around_click(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.locator("#btn").click()
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected
        assert "click" in injected

    def test_inject_monitors_around_hover(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.locator("#menu").hover()
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_select_option(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.locator("#dropdown").select_option("value1")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_dblclick(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.locator("#item").dblclick()
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_expect_to_contain_text(self):
        code = '''from playwright.async_api import Page, expect
async def run_test(page: Page) -> dict:
    await expect(page.locator(".msg")).to_contain_text("Hello")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_expect_to_be_visible(self):
        code = '''from playwright.async_api import Page, expect
async def run_test(page: Page) -> dict:
    await expect(page.locator(".msg")).to_be_visible()
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_screenshot(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.screenshot(path="test.png")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_wait_for_timeout(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.wait_for_timeout(1000)
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_monitors_around_wait_for_load_state(self):
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.wait_for_load_state("networkidle")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_syntax_error_raises_security_exception(self):
        code = "async def run_test(page\n    return"
        with pytest.raises(SecurityException):
            CodeInjector.inject(code)

    def test_inject_non_playwright_returns_original(self):
        code = '''async def run_test(page):
    x = 1 + 1
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert injected == code

    def test_injected_code_is_valid_python(self):
        injected = CodeInjector.inject(MOCK_CODE)
        ast.parse(injected)


class TestExtractOpInfo:
    """_extract_op_info() — Playwright operation detection"""

    def test_page_goto_returns_navigate(self):
        tree = ast.parse("await page.goto('https://example.com')")
        node = tree.body[0].value.value  # Expr -> Await -> Call
        action, target, value = _extract_op_info(node)
        assert action == "navigate"
        assert target == "https://example.com"
        assert value == ""

    def test_fill_returns_fill_with_selector_and_value(self):
        tree = ast.parse("await page.locator('#user').fill('admin')")
        node = tree.body[0].value.value
        action, target, value = _extract_op_info(node)
        assert action == "fill"
        assert target == "#user"
        assert value == "admin"

    def test_click_returns_click_with_selector(self):
        tree = ast.parse("await page.locator('#btn').click()")
        node = tree.body[0].value.value
        action, target, value = _extract_op_info(node)
        assert action == "click"
        assert target == "#btn"
        assert value == ""

    def test_non_playwright_returns_none(self):
        tree = ast.parse("await asyncio.sleep(1)")
        node = tree.body[0].value.value
        action, target, value = _extract_op_info(node)
        assert action is None
        assert target == ""
        assert value == ""


class TestCodeInjectorEdgeCases:
    """inject() 边界分支 — 赋值/参数类型/异常路径"""

    def test_inject_assign_await_operation(self):
        """赋值语句中的 await 操作 → 注入监控"""
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    result = await page.goto("https://example.com")
    return {"success": True, "steps": [], "result": str(result)}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected
        assert "result = await page.goto" in injected

    def test_inject_assign_non_operation_not_injected(self):
        """赋值语句中的非操作 await → 不注入"""
        code = '''import asyncio
async def run_test(page) -> dict:
    x = await asyncio.sleep(1)
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert injected == code

    def test_inject_fstring_target_extracted(self):
        """f-string 选择器 → 提取文字部分"""
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    uid = 42
    await page.locator(f"#user-{uid}").click()
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "#user-{...}" in injected

    def test_inject_name_arg_extracted(self):
        """变量名作为参数 → 提取变量名"""
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    selector = "#btn"
    await page.locator(selector).click()
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "selector" in injected

    def test_inject_expect_to_have_value(self):
        """expect().to_have_value() → assert_text 监控"""
        code = '''from playwright.async_api import Page, expect
async def run_test(page: Page) -> dict:
    await expect(page.locator("#input")).to_have_value("hello")
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "assert_text" in injected

    def test_inject_nested_function_not_injected(self):
        """run_test 之外的函数操作 → 不注入"""
        code = '''from playwright.async_api import Page
async def helper(page: Page) -> None:
    await page.goto("https://example.com")

async def run_test(page: Page) -> dict:
    return {"success": True, "steps": []}
'''
        injected = CodeInjector.inject(code)
        # 无注入，返回原代码
        assert injected == code

    def test_inject_visit_error_raises_security(self, mocker):
        """transformer.visit 抛异常 → SecurityException('AST 注入失败')"""
        code = '''async def run_test(page):
    await page.goto("https://example.com")
    return {"success": True, "steps": []}
'''
        mocker.patch(
            "app.utils.code_injector._MonitorTransformer.visit",
            side_effect=RuntimeError("boom"),
        )
        with pytest.raises(SecurityException, match="AST 注入失败"):
            CodeInjector.inject(code)

    def test_inject_unparse_error_raises_security(self, mocker):
        """ast.unparse 抛异常 → SecurityException('AST 反序列化失败')"""
        code = '''async def run_test(page):
    await page.goto("https://example.com")
    return {"success": True, "steps": []}
'''
        mocker.patch("app.utils.code_injector.ast.unparse", side_effect=RuntimeError("boom"))
        with pytest.raises(SecurityException, match="AST 反序列化失败"):
            CodeInjector.inject(code)