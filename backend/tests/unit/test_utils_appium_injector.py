"""AppiumCodeInjector 单元测试 — Android 同步监控钩子注入"""

import ast
import pytest
from app.utils.appium_code_injector import AppiumCodeInjector
from app.exceptions import SecurityException


# ── 测试代码样本 ──

CHAIN_CLICK_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""

CHAIN_INPUT_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ACCESSIBILITY_ID, "username").send_keys("admin")
    return {"success": True, "steps": []}
"""

UNSUPPORTED_CODE = """def run_test(driver):
    x = 1 + 1
    return {"success": True, "steps": []}
"""

SYNTAX_ERROR_CODE = """def run_test(driver
    driver.find_element(AppiumBy.ID, "btn").click()
"""

MULTI_STEP_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn1").click()
    driver.find_element(AppiumBy.ID, "com.example:id/input").send_keys("hello")
    driver.back()
    driver.find_element(AppiumBy.ID, "com.example:id/btn2").click()
    time.sleep(1)
    return {"success": True, "steps": []}
"""

DRIVER_BACK_CODE = """def run_test(driver):
    driver.back()
    return {"success": True, "steps": []}
"""

DRIVER_SCREENSHOT_CODE = """def run_test(driver):
    driver.save_screenshot("/sdcard/screen.png")
    return {"success": True, "steps": []}
"""

TEXT_ATTR_CODE = """def run_test(driver):
    text = driver.find_element(AppiumBy.ID, "com.example:id/label").text
    return {"success": True, "steps": []}
"""


class TestAppiumCodeInjectorInject:
    """inject() — 主注入方法"""

    def test_inject_chain_click_has_monitors(self):
        """链式 click 调用 → 注入 before/after 监控"""
        injected = AppiumCodeInjector.inject(CHAIN_CLICK_CODE)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_chain_input_has_monitors(self):
        """链式 send_keys 调用 → 注入 before/after 监控"""
        injected = AppiumCodeInjector.inject(CHAIN_INPUT_CODE)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_driver_back_has_monitors(self):
        """driver.back() 直接调用 → 注入监控"""
        injected = AppiumCodeInjector.inject(DRIVER_BACK_CODE)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_driver_screenshot_has_monitors(self):
        """driver.save_screenshot() 直接调用 → 注入监控"""
        injected = AppiumCodeInjector.inject(DRIVER_SCREENSHOT_CODE)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_text_attr_has_monitors(self):
        """driver.find_element(...).text 属性访问 → 注入监控"""
        injected = AppiumCodeInjector.inject(TEXT_ATTR_CODE)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    @pytest.mark.parametrize("code", [
        CHAIN_CLICK_CODE,
        CHAIN_INPUT_CODE,
        DRIVER_BACK_CODE,
        DRIVER_SCREENSHOT_CODE,
        TEXT_ATTR_CODE,
    ])
    def test_injected_code_is_valid_python(self, code):
        """注入后的代码仍是合法 Python"""
        injected = AppiumCodeInjector.inject(code)
        ast.parse(injected)  # 不应抛出异常

    def test_unsupported_action_returns_original(self):
        """不支持的操作 → 返回原代码（不注入）"""
        injected = AppiumCodeInjector.inject(UNSUPPORTED_CODE)
        assert injected == UNSUPPORTED_CODE
        assert "__monitor_before" not in injected

    def test_syntax_error_raises_security_exception(self):
        """语法错误 → 抛出 SecurityException"""
        with pytest.raises(SecurityException, match="语法错误"):
            AppiumCodeInjector.inject(SYNTAX_ERROR_CODE)

    def test_monitor_injection_all_steps(self):
        """多步骤代码 → 每个步骤都注入监控"""
        injected = AppiumCodeInjector.inject(MULTI_STEP_CODE)
        # click x2, send_keys, back, sleep = 5 个步骤
        # 每个步骤有 before 和 after
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected
        # 验证 5 个步骤的编号都存在
        for i in range(1, 6):
            assert f"__monitor_before({i}," in injected or f"__monitor_before({i}," in injected

    def test_monitor_contains_step_numbers(self):
        """监控调用包含正确的步骤编号"""
        injected = AppiumCodeInjector.inject(MULTI_STEP_CODE)
        for i in range(1, 6):
            assert f"__monitor_before({i}," in injected or f"__monitor_before({i}," in injected

    def test_monitor_contains_action_names(self):
        """监控调用包含操作名称"""
        injected = AppiumCodeInjector.inject(MULTI_STEP_CODE)
        assert "click" in injected
        assert "fill" in injected  # send_keys
        assert "back" in injected
        assert "wait" in injected  # time.sleep

    def test_no_playwright_imports_in_injected(self):
        """注入后不应包含 Playwright 相关代码"""
        injected = AppiumCodeInjector.inject(CHAIN_CLICK_CODE)
        assert "playwright" not in injected
        assert "async" not in injected

    def test_error_handler_contains_exception_type(self):
        """异常处理中应包含 exception_type 记录"""
        injected = AppiumCodeInjector.inject(CHAIN_CLICK_CODE)
        assert "exception_type" in injected or "type(__ae)" in injected


class TestAppiumCodeInjectorStepCount:
    """step_count 自增验证"""

    def test_single_step_count_one(self):
        """单步操作 → step_count = 1"""
        injected = AppiumCodeInjector.inject(CHAIN_CLICK_CODE)
        assert "__monitor_before(1," in injected

    def test_multi_step_counts(self):
        """多步操作 → step_count 递增"""
        injected = AppiumCodeInjector.inject(MULTI_STEP_CODE)
        # 验证从 1 到 5 都有
        for i in range(1, 6):
            assert f"__monitor_before({i}," in injected


class TestAppiumCodeInjectorCodeStyle:
    """代码风格约束（由 Validator 拦截，Injector 不处理）"""

    def test_injector_does_not_reject_intermediate_assignments(self):
        """Injector 不拒绝中间变量赋值（应由 Validator 拦截）"""
        code = """def run_test(driver):
    el = driver.find_element(AppiumBy.ID, "com.example:id/btn")
    el.click()
    return {"success": True, "steps": []}
"""
        # Injector 遇到赋值语句中的链式调用不处理，但不应抛出异常
        injected = AppiumCodeInjector.inject(code)
        # 赋值语句中的 find_element 不会触发注入
        assert "__monitor_before" not in injected

    def test_injector_handles_bare_assignments(self):
        """纯赋值语句（不含 find_element）不触发注入"""
        code = """def run_test(driver):
    x = 42
    y = "hello"
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected