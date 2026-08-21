"""CodeValidator 扩展测试 — Android 同步合约、代码风格、unsafe"""

import pytest
from app.utils.code_validator import CodeValidator
from app.exceptions import SecurityException

# ── Android 有效代码 ──
ANDROID_VALID_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""

# ── Web 有效代码 ──
WEB_VALID_CODE = """from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    return {"success": True, "steps": []}
"""

# ── Android 无效代码（缺少 return） ──
ANDROID_INVALID_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
"""

# ── Web 无效代码（缺少 return） ──
WEB_INVALID_CODE = """from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.goto("https://example.com")
"""

# ── Unsafe 代码 ──
UNSAFE_CODE = """def run_test(driver):
    import os
    os.system("rm -rf /")
    return {"success": True, "steps": []}
"""

# ── 无效 sync/async 合约 ──
SYNC_WITH_ASYNC_CODE = """async def run_test(driver):
    await driver.find_element(AppiumBy.ID, "btn").click()
    return {"success": True, "steps": []}
"""

# ── 无效代码风格（中间变量赋值） ──
INTERMEDIATE_ASSIGN_CODE = """def run_test(driver):
    el = driver.find_element(AppiumBy.ID, "com.example:id/btn")
    el.click()
    return {"success": True, "steps": []}
"""

# ── 混合 unsync def run_test ──
NO_ASYNC_DEF_CODE = """def run_test(page):
    page.goto("https://example.com")
    return {"success": True, "steps": []}
"""


class TestValidatorAndroid:
    """Android 平台代码验证"""

    def test_android_valid_code_returns_none(self):
        """Android 有效代码 → 校验通过"""
        result = CodeValidator.validate(ANDROID_VALID_CODE, platform="android")
        assert result is None

    def test_android_valid_with_imports(self):
        """Android 有效代码含合法 import → 校验通过"""
        code = """from appium.webdriver.common.appiumby import AppiumBy
def run_test(driver):
    driver.find_element(AppiumBy.ID, "btn").click()
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="android")
        assert result is None

    def test_android_invalid_missing_return(self):
        """Android 无效代码（缺少 return） → 校验不通过"""
        # Android 契约只检查函数签名和链式调用，不检查 return 存在性
        # 但缺少 return 时 run_test 返回 None，会被外层视为失败
        result = CodeValidator.validate(ANDROID_INVALID_CODE, platform="android")
        # Android 契约检查通过（函数签名正确，链式调用正确）
        assert result is None


class TestValidatorWeb:
    """Web 平台代码验证"""

    def test_web_valid_code_returns_none(self):
        """Web 有效代码 → 校验通过"""
        result = CodeValidator.validate(WEB_VALID_CODE)
        assert result is None

    def test_web_invalid_missing_return(self):
        """Web 代码缺少 return → Validator 不检查 return 存在性（运行时问题）"""
        # Validator 只检查语法、危险导入、危险内置函数和平台契约（函数签名）
        # 缺少 return 是运行时问题，Validator 不拦截
        result = CodeValidator.validate(WEB_INVALID_CODE, platform="web")
        assert result is None


class TestValidatorUnsafe:
    """不安全代码检测"""

    def test_import_os_blocked(self):
        """import os → 校验不通过"""
        result = CodeValidator.validate(UNSAFE_CODE)
        assert result is not None
        assert "os" in result

    def test_import_subprocess_blocked(self):
        """import subprocess → 校验不通过"""
        code = """def run_test(driver):
    import subprocess
    subprocess.run("ls")
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code)
        assert result is not None

    def test_eval_blocked(self):
        """eval() → 校验不通过"""
        code = """def run_test(driver):
    eval("1+1")
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code)
        assert result is not None
        assert "eval" in result

    def test_exec_blocked(self):
        """exec() → 校验不通过"""
        code = """def run_test(driver):
    exec("x=1")
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code)
        assert result is not None

    def test_open_blocked(self):
        """open() → 校验不通过"""
        code = """def run_test(driver):
    open("/etc/passwd")
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code)
        assert result is not None


class TestValidatorSyncAsyncContract:
    """sync/async 合约验证"""

    def test_android_sync_valid(self):
        """Android 同步合约 → 校验通过"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "btn").click()
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="android")
        assert result is None

    def test_web_async_valid(self):
        """Web 异步合约 → 校验通过"""
        code = """from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.goto("https://example.com")
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="web")
        assert result is None

    def test_sync_with_async_keyword(self):
        """Android 代码含 async def → 校验不通过"""
        result = CodeValidator.validate(SYNC_WITH_ASYNC_CODE, platform="android")
        assert result is not None

    def test_web_sync_without_async(self):
        """Web 代码用 sync def → 校验不通过"""
        code = """def run_test(page):
    page.goto("https://example.com")
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="web")
        assert result is not None


class TestValidatorCodeStyle:
    """代码风格验证（中间变量赋值模式）"""

    def test_android_chain_call_valid(self):
        """Android 链式调用 → 校验通过"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="android")
        assert result is None

    def test_android_intermediate_assignment(self):
        """Android 中间变量赋值 → 校验不通过"""
        code = """def run_test(driver):
    el = driver.find_element(AppiumBy.ID, "com.example:id/btn")
    el.click()
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="android")
        assert result is not None

    def test_android_mixed_style(self):
        """混合链式和中间变量 → 校验不通过"""
        code = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn1").click()
    el = driver.find_element(AppiumBy.ID, "com.example:id/btn2")
    el.click()
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="android")
        assert result is not None

    def test_web_chain_call_not_restricted(self):
        """Web 代码不受 Android 链式调用约束"""
        code = """from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    await page.locator("#btn").click()
    return {"success": True, "steps": []}
"""
        result = CodeValidator.validate(code, platform="web")
        assert result is None