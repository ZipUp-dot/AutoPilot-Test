"""代码注入工具 — AST 分析 + 在每个 Playwright 操作前后注入监控钩子

注入原理:
  1. 解析 AI 生成的代码到 AST
  2. 找到 async def run_test 函数
  3. 遍历函数体，识别所有 Playwright 操作（page.goto / locator.fill 等）
  4. 在每个操作前后插入 __monitor_before / __monitor_after 调用
  5. 重新生成代码字符串

如果 AST 结构异常导致注入失败，抛出 SecurityException，拒绝执行。
"""

import ast
import logging
from typing import Optional

from app.exceptions import SecurityException

logger = logging.getLogger("autopilot.injector")

# ── Playwright 操作 → action 名称映射 ──
PLAYWRIGHT_ACTIONS: dict[str, str] = {
    # safe-API（SafePlaywright 白名单方法）
    "safe.goto": "navigate",
    "safe.click": "click",
    "safe.fill": "fill",
    "safe.select": "select",
    "safe.hover": "hover",
    "safe.assert_text": "assert_text",
    "safe.assert_visible": "assert_visible",
    "safe.screenshot": "screenshot",
    "safe.wait": "wait",
    # page-level（旧式，兼容历史代码）
    "page.goto": "navigate",
    "page.screenshot": "screenshot",
    "page.wait_for_timeout": "wait",
    "page.wait_for_load_state": "wait",
    # locator-level
    "fill": "fill",
    "click": "click",
    "select_option": "select",
    "hover": "hover",
    "type": "fill",
    "press": "click",
    "check": "click",
    "uncheck": "click",
    "dblclick": "click",
    # expect-level
    "to_contain_text": "assert_text",
    "to_be_visible": "assert_visible",
    "to_be_hidden": "assert_visible",
    "to_have_text": "assert_text",
    "to_have_value": "assert_text",
}


class CodeInjector:
    """AST 注入监控钩子

    用法:
        try:
            injected = CodeInjector.inject(code)
        except SecurityException as e:
            # 注入失败，拒绝执行
            raise
    """

    @staticmethod
    def inject(code: str) -> str:
        """注入监控代码，返回修改后的代码字符串

        Args:
            code: AI 生成的纯净 Playwright 代码

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
        transformer = _MonitorTransformer()
        try:
            new_tree = transformer.visit(tree)
            ast.fix_missing_locations(new_tree)
        except Exception as e:
            raise SecurityException(f"AST 注入失败: {str(e)}")

        if transformer.step_count == 0:
            logger.warning("未检测到任何 Playwright 操作，注入跳过")
            return code

        try:
            return ast.unparse(new_tree)
        except Exception as e:
            raise SecurityException(f"AST 反序列化失败: {str(e)}")


class _MonitorTransformer(ast.NodeTransformer):
    """AST 变换器：在 run_test 函数中注入监控代码"""

    def __init__(self):
        self.step_count = 0
        self._in_run_test = False

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
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
        """处理表达式语句（await 操作通常是顶层的 Expr）"""
        if not self._in_run_test:
            return node

        if isinstance(node.value, ast.Await):
            wrapped = self._wrap_await(node.value, node)
            if wrapped is not None:
                return wrapped
        return node

    def visit_Assign(self, node: ast.Assign):
        """处理赋值语句（如 result = await page.goto(...)）"""
        if not self._in_run_test:
            return node

        if isinstance(node.value, ast.Await):
            wrapped = self._wrap_await(node.value, node, is_assign=True)
            if wrapped is not None:
                self.step_count += 1
                step_no = self.step_count
                action, target, value = _extract_op_info(node.value.value)
                return _build_try_block(node, step_no, action, target, value, is_assign=True)

        return node

    def _wrap_await(self, await_node: ast.Await, original_stmt, is_assign: bool = False):
        """检查 await 是否是 Playwright 操作，是则生成监控代码块"""
        action, target, value = _extract_op_info(await_node.value)
        if action is None:
            return None

        self.step_count += 1
        step_no = self.step_count
        return _build_try_block(original_stmt, step_no, action, target, value, is_assign)


# ═══════════════════════════════════════════════
# Playwright 操作识别
# ═══════════════════════════════════════════════

def _extract_op_info(node: ast.AST) -> tuple[Optional[str], str, str]:
    """从 AST 节点提取 Playwright 操作信息

    Returns:
        (action_name, target_selector, input_value)
        如果节点不是 Playwright 操作，返回 (None, "", "")
    """
    # 模式: safe.goto(url) / safe.click(sel) / safe.fill(sel, val) 等 Safe API 调用
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        obj = node.func
        if isinstance(obj.value, ast.Name) and obj.value.id == "safe":
            action = PLAYWRIGHT_ACTIONS.get(f"safe.{obj.attr}")
            if action:
                target = _extract_first_arg(node)
                value = _extract_second_arg(node) if action in ("fill", "select", "assert_text") else ""
                return (action, target, value)

    # 模式: page.goto(url)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        obj = node.func
        if isinstance(obj.value, ast.Name) and obj.value.id == "page":
            action = PLAYWRIGHT_ACTIONS.get(f"page.{obj.attr}")
            if action:
                target = _extract_first_arg(node)
                return (action, target, "")

    # 模式: page.locator(sel).fill(val) / .click() / .hover() 等
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        action = PLAYWRIGHT_ACTIONS.get(attr)
        if action:
            # 检查是否是 .locator().xxx() 链式调用
            if isinstance(node.func.value, ast.Call):
                inner = node.func.value
                if isinstance(inner.func, ast.Attribute) and inner.func.attr == "locator":
                    target = _extract_first_arg(inner)
                    value = _extract_first_arg(node) if action in ("fill", "select") else ""
                    return (action, target, value)

    # 模式: expect(page.locator(sel)).to_contain_text(val)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        action = PLAYWRIGHT_ACTIONS.get(attr)
        if action and action.startswith("assert_"):
            # expect(...).to_xxx()
            if isinstance(node.func.value, ast.Call):
                outer = node.func.value
                if isinstance(outer.func, ast.Name) and outer.func.id == "expect":
                    target = _extract_first_arg(outer)
                    value = _extract_first_arg(node)
                    return (action, target, value)

    return (None, "", "")


def _extract_second_arg(call: ast.Call) -> str:
    """提取第二个参数值（字符串或变量名）"""
    if len(call.args) >= 2:
        second = call.args[1]
        if isinstance(second, ast.Constant):
            return str(second.value)
        if isinstance(second, ast.Name):
            return second.id
        if isinstance(second, ast.JoinedStr):
            parts = []
            for p in second.values:
                if isinstance(p, ast.Constant):
                    parts.append(str(p.value))
                elif isinstance(p, ast.FormattedValue):
                    parts.append("{...}")
            return "".join(parts)
    return ""


def _extract_first_arg(call: ast.Call) -> str:
    """提取第一个参数值（字符串或变量名）"""
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant):
            return str(first.value)
        if isinstance(first, ast.Name):
            return first.id
        if isinstance(first, ast.JoinedStr):
            # f-string → 提取文字部分
            parts = []
            for p in first.values:
                if isinstance(p, ast.Constant):
                    parts.append(str(p.value))
                elif isinstance(p, ast.FormattedValue):
                    parts.append("{...}")
            return "".join(parts)
    return ""


# ═══════════════════════════════════════════════
# 构建注入代码的 AST 节点
# ═══════════════════════════════════════════════

def _make_monitor_before(step_no: int, action: str, target: str, value: str) -> ast.Expr:
    """生成 await __monitor_before(step_no, action, target, value) 调用"""
    return ast.Expr(value=ast.Await(value=ast.Call(
        func=ast.Name(id="__monitor_before", ctx=ast.Load()),
        args=[
            ast.Constant(value=step_no),
            ast.Constant(value=action),
            ast.Constant(value=target),
            ast.Constant(value=value),
        ],
        keywords=[],
    )))


def _make_monitor_after(step_no: int, status: str, error_msg: ast.expr | str) -> ast.Expr:
    """生成 await __monitor_after(step_no, status, error_msg) 调用"""
    if isinstance(error_msg, str):
        error_msg = ast.Constant(value=error_msg)
    return ast.Expr(value=ast.Await(value=ast.Call(
        func=ast.Name(id="__monitor_after", ctx=ast.Load()),
        args=[
            ast.Constant(value=step_no),
            ast.Constant(value=status),
            error_msg,
        ],
        keywords=[],
    )))


def _build_try_block(
    original_stmt: ast.stmt,
    step_no: int,
    action: str,
    target: str,
    value: str,
    is_assign: bool = False,
) -> list[ast.stmt]:
    """构建 try/except/else 监控代码块

    生成:
        __monitor_before(step_no, action, target, value)
        try:
            original_stmt          # 或被赋值为 __ae_result
        except Exception as __ae:
            __monitor_after(step_no, 'failed', str(__ae))
            raise
        else:
            __monitor_after(step_no, 'passed', '')
    """
    before = _make_monitor_before(step_no, action, target, value)

    # try body
    if is_assign:
        # 保存原始变量名，用临时变量接结果
        assign_stmt = original_stmt
        try_body = [assign_stmt]
    elif isinstance(original_stmt, ast.Expr):
        try_body = [original_stmt]
    else:
        try_body = [original_stmt]

    # except handler
    except_handler = ast.ExceptHandler(
        type=ast.Name(id="Exception", ctx=ast.Load()),
        name="__ae",
        body=[
            _make_monitor_after(step_no, "failed",
                ast.Call(
                    func=ast.Name(id="str", ctx=ast.Load()),
                    args=[ast.Name(id="__ae", ctx=ast.Load())],
                    keywords=[],
                )),
            ast.Raise(exc=None, cause=None),
        ],
    )

    # else body
    else_body = [
        _make_monitor_after(step_no, "passed", ast.Constant(value="")),
    ]

    try_node = ast.Try(
        body=try_body,
        handlers=[except_handler],
        orelse=else_body,
        finalbody=[],
    )

    return [before, try_node]
