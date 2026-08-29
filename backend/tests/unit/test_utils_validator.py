"""CodeValidator unit tests — AST security checks and syntax validation."""

import pytest

from app.utils.code_validator import CodeValidator, _check_imports, _check_builtins, _has_run_test, BANNED_MODULES, BANNED_BUILTINS
from app.exceptions import SecurityException

import ast

VALID_CODE = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    return {"success": True, "steps": []}
'''


class TestCodeValidatorValidate:
    """validate() — full security + syntax + run_test check"""

    def test_valid_code_returns_none(self):
        result = CodeValidator.validate(VALID_CODE)
        assert result is None

    def test_import_os_blocked(self):
        code = "import os\n" + VALID_CODE
        result = CodeValidator.validate(code)
        assert result is not None
        assert "os" in result

    def test_from_os_import_blocked(self):
        code = "from os import path\n" + VALID_CODE
        result = CodeValidator.validate(code)
        assert result is not None
        assert "os" in result

    def test_import_subprocess_blocked(self):
        code = "import subprocess\n" + VALID_CODE
        result = CodeValidator.validate(code)
        assert result is not None

    def test_import_socket_blocked(self):
        code = "import socket\n" + VALID_CODE
        result = CodeValidator.validate(code)
        assert result is not None

    def test_import_sys_blocked(self):
        code = "import sys\n" + VALID_CODE
        result = CodeValidator.validate(code)
        assert result is not None

    def test_eval_blocked(self):
        code = "async def run_test(page):\n    eval('1+1')\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is not None
        assert "eval" in result

    def test_exec_blocked(self):
        code = "async def run_test(page):\n    exec('x=1')\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is not None
        assert "exec" in result

    def test_open_blocked(self):
        code = "async def run_test(page):\n    open('/etc/passwd')\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is not None
        assert "open" in result

    def test_dunder_import_blocked(self):
        code = "async def run_test(page):\n    __import__('os')\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is not None
        assert "__import__" in result

    def test_compile_blocked(self):
        code = "async def run_test(page):\n    compile('x=1', '', 'exec')\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is not None

    def test_getattr_blocked(self):
        code = "async def run_test(page):\n    getattr(page, 'goto')\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is not None

    def test_missing_run_test(self):
        code = "x = 1\nprint(x)"
        result = CodeValidator.validate(code)
        assert result is not None
        assert "run_test" in result

    def test_syntax_error(self):
        code = "async def run_test(page\n    return"
        result = CodeValidator.validate(code)
        assert result is not None
        assert "语法错误" in result

    def test_has_run_test_no_dangerous_code(self):
        code = "async def run_test(page):\n    x = 1\n    return {'success': True}"
        result = CodeValidator.validate(code)
        assert result is None


class TestCodeValidatorCheckSyntax:
    """check_syntax() — syntax-only validation"""

    def test_valid_syntax_returns_none(self):
        assert CodeValidator.check_syntax("x = 1") is None

    def test_invalid_syntax_returns_error(self):
        result = CodeValidator.check_syntax("x =")
        assert result is not None
        assert "语法错误" in result


class TestHasRunTest:
    """_has_run_test() — detect async def run_test"""

    def test_async_def_run_test_returns_true(self):
        tree = ast.parse("async def run_test(page):\n    return {'success': True}")
        assert _has_run_test(tree) is True

    def test_no_run_test_returns_false(self):
        tree = ast.parse("def foo():\n    pass")
        assert _has_run_test(tree) is False

    def test_sync_run_test_returns_false(self):
        tree = ast.parse("def run_test(page):\n    return {'success': True}")
        assert _has_run_test(tree) is False


class TestCheckImports:
    """_check_imports() — blacklist module detection"""

    def test_import_json_returns_none(self):
        tree = ast.parse("import json")
        assert _check_imports(tree) is None

    def test_import_requests_returns_error(self):
        tree = ast.parse("import requests")
        assert _check_imports(tree) is not None

    def test_from_import_requests_returns_error(self):
        tree = ast.parse("from requests import get")
        assert _check_imports(tree) is not None


class TestCheckBuiltins:
    """_check_builtins() — dangerous builtin detection"""

    def test_print_returns_none(self):
        tree = ast.parse("print('hello')")
        assert _check_builtins(tree) is None

    def test_setattr_returns_error(self):
        tree = ast.parse("setattr(obj, 'x', 1)")
        assert _check_builtins(tree) is not None

    def test_delattr_returns_error(self):
        tree = ast.parse("delattr(obj, 'x')")
        assert _check_builtins(tree) is not None


class TestSafePlaywrightValidation:
    """SafePlaywright 安全校验 — 禁止绕过 Safe API 获取原生 page"""

    # ── 合法 Safe API 代码 ──
    def test_legitimate_safe_api_code_passes(self):
        code = '''async def run_test(safe) -> dict:
    await safe.goto("https://example.com")
    await safe.click("#btn")
    await safe.fill("#user", "admin")
    await safe.assert_text(".msg", "Hello")
    await safe.screenshot(path="a.png")
    await safe.wait(1000)
    return {"success": True, "steps": []}
'''
        assert CodeValidator.validate(code) is None

    def test_legitimate_playwright_code_passes(self):
        """合法 Playwright 测试代码（仅 import 类型，不操作原生对象）"""
        code = '''from playwright.async_api import Page
async def run_test(page: Page) -> dict:
    return {"success": True, "steps": []}
'''
        assert CodeValidator.validate(code) is None

    # ── 直接访问原生 page ──
    def test_direct_page_goto_blocked(self):
        code = '''async def run_test(safe) -> dict:
    await page.goto("https://example.com")
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "page.goto" in result

    def test_direct_page_locator_blocked(self):
        code = '''async def run_test(safe) -> dict:
    await page.locator("#btn").click()
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "page.locator" in result

    # ── 私有属性访问 ──
    def test_private_attr_blocked(self):
        code = '''async def run_test(safe) -> dict:
    await safe._page.goto("https://example.com")
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "_page" in result

    # ── 魔法属性访问 ──
    def test_dunder_class_blocked(self):
        code = '''async def run_test(safe) -> dict:
    obj = safe.__class__
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "__class__" in result

    def test_dunder_dict_blocked(self):
        code = '''async def run_test(safe) -> dict:
    obj = safe.__dict__
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "__dict__" in result

    def test_dunder_getattribute_blocked(self):
        code = '''async def run_test(safe) -> dict:
    obj = safe.__getattribute__("_page")
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "__getattribute__" in result

    # ── 反射绕过 ──
    def test_type_reflection_blocked(self):
        code = '''async def run_test(safe) -> dict:
    cls = type(safe)
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "type" in result

    def test_object_reflection_blocked(self):
        code = '''async def run_test(safe) -> dict:
    obj = object.__getattribute__
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "object" in result

    def test_vars_reflection_blocked(self):
        code = '''async def run_test(safe) -> dict:
    obj = vars(safe)
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "vars" in result

    def test_dir_reflection_blocked(self):
        code = '''async def run_test(safe) -> dict:
    obj = dir(safe)
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "dir" in result

    # ── 危险 import / builtin 仍拦截 ──
    def test_dangerous_import_still_blocked(self):
        code = '''import os
async def run_test(safe) -> dict:
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "os" in result

    def test_dangerous_builtin_still_blocked(self):
        code = '''async def run_test(safe) -> dict:
    eval("1+1")
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code)
        assert result is not None
        assert "eval" in result