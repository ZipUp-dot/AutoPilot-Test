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