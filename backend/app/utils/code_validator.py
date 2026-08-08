"""代码安全校验 — AST 遍历检查黑名单模块和危险内置函数"""

import ast
import logging
from typing import Optional

from app.exceptions import SecurityException

logger = logging.getLogger("autopilot.validator")

# ── 危险导入黑名单 ──
BANNED_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "requests",
    "urllib", "ftplib", "smtplib", "shutil", "signal",
    "ctypes", "multiprocessing", "pickle", "marshal",
})

# ── 危险内置函数黑名单 ──
BANNED_BUILTINS = frozenset({
    "eval", "exec", "open", "compile", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals",
})


class CodeValidator:
    """AST 安全校验 + 语法检查

    用法:
        error = CodeValidator.validate(code)
        if error:
            raise SecurityException(error)
    """

    @staticmethod
    def validate(code: str) -> Optional[str]:
        """校验代码安全性，返回错误消息或 None

        Args:
            code: Python 源代码

        Returns:
            错误消息字符串，如果安全则返回 None
        """
        # 1. 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"语法错误 (行 {e.lineno}, 列 {e.offset}): {e.msg}"

        # 2. 检查危险导入
        error = _check_imports(tree)
        if error:
            return error

        # 3. 检查危险内置函数
        error = _check_builtins(tree)
        if error:
            return error

        # 4. 检查是否有 run_test 函数
        if not _has_run_test(tree):
            return "代码缺少 async def run_test(page) 函数"

        return None

    @staticmethod
    def check_syntax(code: str) -> Optional[str]:
        """仅语法检查，不检查安全性"""
        try:
            ast.parse(code)
            return None
        except SyntaxError as e:
            return f"语法错误 (行 {e.lineno}): {e.msg}"


def _check_imports(tree: ast.AST) -> Optional[str]:
    """检查 import 语句中的黑名单模块"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_MODULES:
                    return f"禁止导入模块: {root} (行 {node.lineno})"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in BANNED_MODULES:
                    return f"禁止导入模块: {root} (行 {node.lineno})"
    return None


def _check_builtins(tree: ast.AST) -> Optional[str]:
    """检查是否使用了危险内置函数"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_BUILTINS:
                return f"禁止使用函数: {func.id}() (行 {node.lineno})"
    return None


def _has_run_test(tree: ast.AST) -> bool:
    """检查是否包含 async def run_test(page) 函数"""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_test":
            return True
    return False
