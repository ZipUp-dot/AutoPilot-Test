"""代码安全校验 — AST 遍历检查黑名单模块、危险内置函数、平台契约

平台感知：
  - Web (platform="web"):  async def run_test(page) — 异步，不强制链式风格
  - Android (platform="android"):  def run_test(driver) — 同步，强制链式风格

职责边界：
  - 不符合链式调用风格的代码（中间变量赋值后再操作）由 Validator 在注入前拒绝
  - 不让 CodeInjector 尝试处理或静默跳过无法识别的 AST 模式
  - 禁止仅通过参数名称 page/driver 判断合法性
"""

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

# ── Android 链式调用：driver.find_element 结尾的方法名 ──
_ANDROID_CHAINABLE_METHODS = frozenset({
    "click", "send_keys", "clear", "submit",
    "get_attribute", "is_displayed", "is_enabled", "is_selected",
    "text", "location", "size", "rect",
})


class CodeValidator:
    """AST 安全校验 + 语法检查 + 平台契约检查

    用法:
        error = CodeValidator.validate(code, platform="web")
        if error:
            raise SecurityException(error)
    """

    @staticmethod
    def validate(code: str, platform: str = "web") -> Optional[str]:
        """校验代码安全性和平台契约，返回错误消息或 None

        Args:
            code: Python 源代码
            platform: "web" 或 "android"

        Returns:
            错误消息字符串，如果通过则返回 None
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

        # 4. 平台契约检查
        if platform == "android":
            error = _check_android_contract(tree)
        else:
            error = _check_web_contract(tree)
        if error:
            return error

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


# ═══════════════════════════════════════════════
# Web 契约
# ═══════════════════════════════════════════════

def _check_web_contract(tree: ast.AST) -> Optional[str]:
    """检查 Web 平台契约：async def run_test(page)"""
    run_test_node = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_test":
            run_test_node = node
            break

    if run_test_node is None:
        return "Web 代码缺少 async def run_test(page) 函数"

    # 参数数量：必须为 1
    args = run_test_node.args.args
    if len(args) != 1:
        return f"Web run_test 必须接收 1 个参数 (page)，当前为 {len(args)} 个参数"

    return None


# ═══════════════════════════════════════════════
# Android 契约
# ═══════════════════════════════════════════════

def _check_android_contract(tree: ast.AST) -> Optional[str]:
    """检查 Android 平台契约：def run_test(driver) + 链式调用

    Requirements:
      1. 函数名必须为 run_test
      2. 必须是同步函数（def，非 async def）
      3. 必须接收 1 个参数
      4. 所有元素操作必须是链式调用，禁止中间变量赋值
    """
    run_test_node = None
    for node in ast.iter_child_nodes(tree):
        # 必须是同步 def，不能是 async def
        if isinstance(node, ast.FunctionDef) and not isinstance(node, ast.AsyncFunctionDef) and node.name == "run_test":
            run_test_node = node
            break

    if run_test_node is None:
        return "Android 代码缺少 def run_test(driver) 函数"

    # 参数数量：必须为 1
    args = run_test_node.args.args
    if len(args) != 1:
        return f"Android run_test 必须接收 1 个参数 (driver)，当前为 {len(args)} 个参数"

    # 检查链式调用：禁止中间变量赋值
    error = _check_android_chained_calls(run_test_node)
    if error:
        return error

    return None


def _check_android_chained_calls(func_node: ast.FunctionDef) -> Optional[str]:
    """检查 Android 函数体中是否包含禁止的中间变量赋值模式

    禁止模式：
      el = driver.find_element(...)
      el.click()

    允许模式：
      driver.find_element(...).click()
      driver.find_element(...).send_keys(...)
    """
    # 第一遍：收集所有 driver.find_element 赋值的变量
    assigns = {}
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
                if isinstance(node.value, ast.Call):
                    call = node.value
                    if _is_driver_find_element_call(call):
                        assigns[target_name] = node.lineno

    if not assigns:
        return None

    # 第二遍：检查是否有变量被使用
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id in assigns:
            # 跳过赋值语句本身（el = driver.find_element(...)）
            parent = _get_parent(func_node, node)
            if isinstance(parent, ast.Assign) and node in parent.targets:
                continue
            assign_line = assigns[node.id]
            return (
                f"Android 代码禁止中间变量赋值后操作 (行 {assign_line} 赋值 → 行 {node.lineno} 使用): "
                f"变量 '{node.id}' 是 driver.find_element 的结果，"
                f"请使用链式调用: driver.find_element(...).action()"
            )

    return None


def _get_parent(root: ast.AST, target: ast.AST) -> Optional[ast.AST]:
    """在 AST 树中查找 target 节点的父节点"""
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            if child is target:
                return node
    return None


def _is_driver_find_element_call(node: ast.Call) -> bool:
    """检查是否是 driver.find_element(...) 调用"""
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "find_element":
        return False
    if not isinstance(node.func.value, ast.Name):
        return False
    return node.func.value.id == "driver"


def _has_run_test(tree: ast.AST) -> bool:
    """检查是否包含 async def run_test(page) 函数（Web 兼容）"""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_test":
            return True
    return False