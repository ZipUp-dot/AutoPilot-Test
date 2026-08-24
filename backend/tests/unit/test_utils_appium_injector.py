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


class TestAppiumCodeInjectorEdgeCases:
    """inject() 边界分支 — 更多链式操作/参数类型/异常路径"""

    def test_inject_chain_clear(self):
        """链式 clear() → fill 动作注入"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/input").clear()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "__monitor_after" in injected

    def test_inject_chain_submit(self):
        """链式 submit() → click 动作注入"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.XPATH, "//form").submit()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "click" in injected

    def test_inject_chain_is_displayed(self):
        """链式 is_displayed() → assert_visible 动作注入"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/msg").is_displayed()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "assert_visible" in injected

    def test_inject_chain_get_attribute(self):
        """链式 get_attribute() → assert_text 动作注入"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/label").get_attribute("text")
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "assert_text" in injected

    def test_inject_driver_swipe(self):
        """driver.swipe() 多参数 → 目标为 4 个坐标拼接"""
        code = """def run_test(driver):
    driver.swipe(500, 1000, 300, 300)
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "500,1000,300,300" in injected

    def test_inject_driver_get_screenshot_as_file(self):
        """driver.get_screenshot_as_file() → screenshot 动作"""
        code = """def run_test(driver):
    driver.get_screenshot_as_file("/sdcard/s.png")
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "screenshot" in injected
        assert "/sdcard/s.png" in injected

    def test_inject_driver_launch_app(self):
        """driver.launch_app() → navigate 动作"""
        code = """def run_test(driver):
    driver.launch_app()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "navigate" in injected

    def test_inject_driver_implicitly_wait(self):
        """driver.implicitly_wait() → wait 动作"""
        code = """def run_test(driver):
    driver.implicitly_wait(5)
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "wait" in injected

    def test_inject_driver_current_activity(self):
        """driver.current_activity() → navigate 动作"""
        code = """def run_test(driver):
    driver.current_activity()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "navigate" in injected

    def test_inject_driver_property_access_not_injected(self):
        """driver 属性访问（非调用）→ 不注入"""
        code = """def run_test(driver):
    driver.current_activity
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert injected == code

    def test_inject_fstring_target(self):
        """f-string 定位值 → 提取文字部分"""
        code = """def run_test(driver):
    uid = 42
    driver.find_element(AppiumBy.ID, f"com.example:id/user_{uid}").click()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert "__monitor_before" in injected
        assert "com.example:id/user_{...}" in injected

    def test_inject_nested_function_not_injected(self):
        """run_test 之外函数中的操作 → 不注入"""
        code = """def helper(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()

def run_test(driver):
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert injected == code

    def test_inject_non_driver_chain_not_injected(self):
        """非 driver 对象链式调用 → 不注入"""
        code = """def run_test(driver):
    other_obj.find_element(AppiumBy.ID, "x").click()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert injected == code

    def test_inject_driver_unknown_method_not_injected(self):
        """driver 未映射方法 → 不注入"""
        code = """def run_test(driver):
    driver.unknown_method()
    return {"success": True, "steps": []}
"""
        injected = AppiumCodeInjector.inject(code)
        assert injected == code

    def test_inject_visit_error_raises_security(self, mocker):
        """transformer.visit 抛异常 → SecurityException('AST 注入失败')"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""
        mocker.patch(
            "app.utils.appium_code_injector._AppiumMonitorTransformer.visit",
            side_effect=RuntimeError("boom"),
        )
        with pytest.raises(SecurityException, match="AST 注入失败"):
            AppiumCodeInjector.inject(code)

    def test_inject_unparse_error_raises_security(self, mocker):
        """ast.unparse 抛异常 → SecurityException('AST 反序列化失败')"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""
        mocker.patch("app.utils.appium_code_injector.ast.unparse", side_effect=RuntimeError("boom"))
        with pytest.raises(SecurityException, match="AST 反序列化失败"):
            AppiumCodeInjector.inject(code)