"""Appium 代码注入工具 — AST 分析 + 在每个 Android 操作前后注入同步监控钩子

注入原理:
  1. 解析 AI 生成的代码到 AST
  2. 找到 def run_test 函数（同步，非 async）
  3. 遍历函数体，识别所有 Appium 操作
  4. 在每个操作前后插入 __monitor_before / __monitor_after 调用（同步，无 await）
  5. 重新生成代码字符串

如果 AST 结构异常导致注入失败，抛出 SecurityException，拒绝执行。

注入后的代码只存在本次执行内存中，不写回数据库。
"""

import ast
import logging
from typing import Optional

from app.exceptions import SecurityException

logger = logging.getLogger("autopilot.appium_injector")

# ── 链式操作 → action 名称映射（driver.find_element(...).method()） ──
APPIUM_CHAIN_ACTIONS: dict[str, str] = {
    "click": "click",
    "send_keys": "fill",
    "clear": "fill",
    "submit": "click",
    "is_displayed": "assert_visible",
    "is_enabled": "assert_visible",
    "is_selected": "assert_visible",
    "get_attribute": "assert_text",
}

# ── driver 直接调用 → action 名称映射（driver.back() 等） ──
APPIUM_DRIVER_ACTIONS: dict[str, str] = {
    "back": "back",
    "swipe": "swipe",
    "save_screenshot": "screenshot",
    "get_screenshot_as_file": "screenshot",
    "get_screenshot_as_png": "screenshot",
    "hide_keyboard": "click",
    "launch_app": "navigate",
    "close_app": "navigate",
    "terminate_app": "navigate",
    "activate_app": "navigate",
    "current_activity": "navigate",
    "current_package": "navigate",
    "get_page_source": "screenshot",
    "implicitly_wait": "wait",
}

# ── 其他模块调用 → action 名称映射 ──
APPIUM_OTHER_ACTIONS: dict[str, str] = {
    "sleep": "wait",  # time.sleep()
}


class AppiumCodeInjector:
    """AST 注入同步监控钩子（Android）

    用法:
        try:
            injected = AppiumCodeInjector.inject(code)
        except SecurityException as e:
            # 注入失败，拒绝执行
            raise
    """

    @staticmethod
    def inject(code: str) -> str:
        """注入监控代码，返回修改后的代码字符串

        Args:
            code: AI 生成的纯净 Appium 代码

        Returns:
            注入 __monitor_before/after 调用的代码

        Raises:
            SecurityException: AST 解析或注入失败
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise SecurityException(f"代码语法错误，无法注入: {e.msg} (行 {e.lineno})")

        # 找到 run_test 函数
        transformer = _AppiumMonitorTransformer()
        try:
            new_tree = transformer.visit(tree)
            ast.fix_missing_locations(new_tree)
        except Exception as e:
            raise SecurityException(f"AST 注入失败: {str(e)}")

        if transformer.step_count == 0:
            logger.warning("未检测到任何 Appium 操作，注入跳过")
            return code

        try:
            return ast.unparse(new_tree)
        except Exception as e:
            raise SecurityException(f"AST 反序列化失败: {str(e)}")


class _AppiumMonitorTransformer(ast.NodeTransformer):
    """AST 变换器：在 run_test 函数中注入同步监控代码"""

    def __init__(self):
        self.step_count = 0
        self._in_run_test = False

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """处理同步 def run_test(driver) 函数"""
        if node.name == "run_test":
            self._in_run_test = True
            self.step_count = 0
            new_body = []
            for stmt in node.body:
                transformed = self.visit(stmt)
                if isinstance(transformed, list):
                    new_body.extend(transformed)
                else:
                    new_body.append(transformed)
            node.body = new_body
            self._in_run_test = False
        return node

    def visit_Expr(self, node: ast.Expr):
        """处理表达式语句（链式操作、driver 直接调用等）"""
        if not self._in_run_test:
            return node

        if not isinstance(node.value, ast.Call):
            return node

        # 1) driver.find_element(AppiumBy.X, val).method() 链式调用
        info = _extract_chain_call_info(node.value)
        if info is not None:
            self.step_count += 1
            action, target, value = info
            return _build_sync_try_block(node, self.step_count, action, target, value)

        # 2) driver.back() / driver.swipe() / driver.save_screenshot() 等
        info = _extract_driver_call_info(node.value)
        if info is not None:
            self.step_count += 1
            action, target, value = info
            return _build_sync_try_block(node, self.step_count, action, target, value)

        # 3) time.sleep() 等
        info = _extract_other_call_info(node.value)
        if info is not None:
            self.step_count += 1
            action, target, value = info
            return _build_sync_try_block(node, self.step_count, action, target, value)

        return node

    def visit_Assign(self, node: ast.Assign):
        """处理赋值语句（如 text = driver.find_element(...).text）"""
        if not self._in_run_test:
            return node

        # text = driver.find_element(AppiumBy.X, val).text
        if isinstance(node.value, ast.Attribute):
            info = _extract_text_attr_call_info(node.value)
            if info is not None:
                self.step_count += 1
                action, target, value = info
                return _build_sync_try_block(node, self.step_count, action, target, value, is_assign=True)

        return node


# ═══════════════════════════════════════════════
# Appium 操作识别
# ═══════════════════════════════════════════════

def _extract_chain_call_info(node: ast.Call) -> Optional[tuple[str, str, str]]:
    """提取 driver.find_element(AppiumBy.X, val).method() 链式调用信息

    Returns:
        (action_name, target_selector, input_value)
        如果非链式调用返回 None
    """
    if not isinstance(node.func, ast.Attribute):
        return None

    method_name = node.func.attr
    action = APPIUM_CHAIN_ACTIONS.get(method_name)
    if action is None:
        return None

    # 检查是否是 .find_element().xxx() 链式调用
    if not isinstance(node.func.value, ast.Call):
        return None

    inner = node.func.value  # driver.find_element(...)
    if not isinstance(inner.func, ast.Attribute):
        return None
    if inner.func.attr != "find_element":
        return None
    if not isinstance(inner.func.value, ast.Name):
        return None
    if inner.func.value.id != "driver":
        return None

    # 提取 target = find_element 的第二个参数（AppiumBy 策略后的 value）
    target = _extract_appium_target(inner)

    # 提取 value = 链式调用的第一个参数（send_keys 的文本等）
    value = _extract_first_arg(node) if action in ("fill",) else ""

    return (action, target, value)


def _extract_driver_call_info(node: ast.Call) -> Optional[tuple[str, str, str]]:
    """提取 driver.method() 直接调用信息"""
    if not isinstance(node.func, ast.Attribute):
        return None
    if not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id != "driver":
        return None

    method_name = node.func.attr
    action = APPIUM_DRIVER_ACTIONS.get(method_name)
    if action is None:
        return None

    # 提取参数
    if action == "swipe":
        target = ",".join(str(_extract_first_arg_by_index(node, i)) for i in range(4))
        target = target or ""
    elif action == "screenshot":
        target = _extract_first_arg(node)
    else:
        target = ""

    return (action, target, "")


def _extract_other_call_info(node: ast.Call) -> Optional[tuple[str, str, str]]:
    """提取非 driver 调用（如 time.sleep()）信息"""
    if not isinstance(node.func, ast.Attribute):
        return None

    method_name = node.func.attr
    action = APPIUM_OTHER_ACTIONS.get(method_name)
    if action is None:
        return None

    if not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id != "time":
        return None

    target = _extract_first_arg(node)
    return (action, target, "")


def _extract_text_attr_call_info(node: ast.Attribute) -> Optional[tuple[str, str, str]]:
    """提取 text = driver.find_element(...).text 属性访问信息

    Returns:
        (action_name, target_selector, "")
        如果非 find_element 属性访问返回 None
    """
    if node.attr != "text":
        return None

    if not isinstance(node.value, ast.Call):
        return None

    inner = node.value
    if not isinstance(inner.func, ast.Attribute):
        return None
    if inner.func.attr != "find_element":
        return None
    if not isinstance(inner.func.value, ast.Name):
        return None
    if inner.func.value.id != "driver":
        return None

    target = _extract_appium_target(inner)
    return ("assert_text", target, "")


# ═══════════════════════════════════════════════
# 参数提取
# ═══════════════════════════════════════════════

def _extract_appium_target(call: ast.Call) -> str:
    """提取 find_element 的第二个参数（定位值）"""
    if len(call.args) >= 2:
        return _extract_arg_value(call.args[1])
    return ""


def _extract_first_arg(call: ast.Call) -> str:
    """提取第一个参数值"""
    if call.args:
        return _extract_arg_value(call.args[0])
    return ""


def _extract_first_arg_by_index(call: ast.Call, index: int) -> str:
    """按索引提取参数值"""
    if index < len(call.args):
        return _extract_arg_value(call.args[index])
    return ""


def _extract_arg_value(arg: ast.expr) -> str:
    """从 AST 表达式节点提取字符串值"""
    if isinstance(arg, ast.Constant):
        return str(arg.value)
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.JoinedStr):
        parts = []
        for p in arg.values:
            if isinstance(p, ast.Constant):
                parts.append(str(p.value))
            elif isinstance(p, ast.FormattedValue):
                parts.append("{...}")
        return "".join(parts)
    return ""


# ═══════════════════════════════════════════════
# 构建注入代码的 AST 节点（同步版）
# ═══════════════════════════════════════════════

def _make_sync_monitor_before(step_no: int, action: str, target: str, value: str) -> ast.Expr:
    """生成 __monitor_before(step_no, action, target, value) 调用（同步）"""
    return ast.Expr(value=ast.Call(
        func=ast.Name(id="__monitor_before", ctx=ast.Load()),
        args=[
            ast.Constant(value=step_no),
            ast.Constant(value=action),
            ast.Constant(value=target),
            ast.Constant(value=value),
        ],
        keywords=[],
    ))


def _make_sync_monitor_after(step_no: int, status: str, error_msg: ast.expr | str, exc_type: ast.expr | str = "") -> ast.Expr:
    """生成 __monitor_after(step_no, status, error_msg, exception_type) 调用（同步）"""
    if isinstance(error_msg, str):
        error_msg = ast.Constant(value=error_msg)
    if isinstance(exc_type, str):
        exc_type = ast.Constant(value=exc_type)
    return ast.Expr(value=ast.Call(
        func=ast.Name(id="__monitor_after", ctx=ast.Load()),
        args=[
            ast.Constant(value=step_no),
            ast.Constant(value=status),
            error_msg,
            exc_type,
        ],
        keywords=[],
    ))


def _build_sync_try_block(
    original_stmt: ast.stmt,
    step_no: int,
    action: str,
    target: str,
    value: str,
    is_assign: bool = False,
) -> list[ast.stmt]:
    """构建同步 try/except/else 监控代码块

    生成:
        __monitor_before(step_no, action, target, value)
        try:
            original_stmt
        except Exception as __ae:
            __monitor_after(step_no, 'failed', str(__ae))
            raise
        else:
            __monitor_after(step_no, 'passed', '')

    Args:
        original_stmt: 原始语句
        step_no: 步骤编号
        action: 操作类型
        target: 目标选择器
        value: 输入值
        is_assign: 是否为赋值语句（如 text = driver.find_element(...).text）
    """
    before = _make_sync_monitor_before(step_no, action, target, value)

    # try body
    try_body = [original_stmt]

    # except handler
    except_handler = ast.ExceptHandler(
        type=ast.Name(id="Exception", ctx=ast.Load()),
        name="__ae",
        body=[
            _make_sync_monitor_after(step_no, "failed",
                error_msg=ast.Call(
                    func=ast.Name(id="str", ctx=ast.Load()),
                    args=[ast.Name(id="__ae", ctx=ast.Load())],
                    keywords=[],
                ),
                exc_type=ast.Attribute(
                    value=ast.Call(
                        func=ast.Name(id="type", ctx=ast.Load()),
                        args=[ast.Name(id="__ae", ctx=ast.Load())],
                        keywords=[],
                    ),
                    attr="__name__",
                    ctx=ast.Load(),
                )),
            ast.Raise(exc=None, cause=None),
        ],
    )

    # else body
    else_body = [
        _make_sync_monitor_after(step_no, "passed", ast.Constant(value="")),
    ]

    try_node = ast.Try(
        body=try_body,
        handlers=[except_handler],
        orelse=else_body,
        finalbody=[],
    )

    return [before, try_node]