"""SafePlaywright — 受限的 Playwright page 包装器

AI 生成的代码只能通过 SafePlaywright 调用受控 API，
无法访问原生 page 对象，也无法通过反射（__class__ / __dict__
/ __getattribute__ / 私有属性）绕过限制获取底层对象。

安全设计：
  - __slots__ 禁止实例动态添加属性
  - __getattribute__ 拦截所有下划线开头属性（含 _page / __class__ / __dict__）
  - __setattr__ 禁止修改任何属性
  - 仅暴露白名单方法（goto / click / fill / select / hover / assert_* / screenshot / wait）
"""

from typing import Any, Optional


# ── 允许暴露给 AI 代码的方法白名单 ──
_SAFE_METHODS = frozenset({
    "goto", "click", "fill", "select", "hover",
    "assert_text", "assert_visible", "screenshot", "wait",
})

# ── 供 Playwright 内部栈分析读取的受限类替身 ──
# Playwright 1.56 在每次 API 调用（wrap_api_call → _extract_stack_trace...）
# 都会遍历调用栈并读取 frame.f_locals["self"].__class__.__name__ 来构建
# API 名。SafePlaywright 的自定义 __getattribute__ 若拦截 __class__，
# 真实浏览器中所有 SafePlaywright 操作都会抛 AttributeError 并被掩盖。
# 因此对 __class__ 放行一个无实际能力的假类（仅提供 __name__），
# 而 _page / __dict__ / __getattribute__ 等私有/魔法属性仍全部拦截。
# AI 代码层面由 CodeValidator 禁止访问 safe.__class__（AST 私有属性拦截）。
_SAFE_CLASS_PROXY = type("SafePlaywright", (), {"__name__": "SafePlaywright"})


class SafePlaywright:
    """包装原生 Playwright page，仅暴露白名单方法。"""

    __slots__ = ("_page",)

    def __init__(self, page: Any) -> None:
        object.__setattr__(self, "_page", page)

    def __getattribute__(self, name: str) -> Any:
        # Playwright 栈分析兼容：仅放行 __class__（假类），其余私有/魔法属性一律拦截
        if name == "__class__":
            return _SAFE_CLASS_PROXY
        # 禁止访问任何私有/魔法属性（_page, __dict__, __getattribute__ 等）
        if name.startswith("_"):
            raise AttributeError(f"禁止访问私有属性: {name}")
        # 仅允许白名单方法，阻断任意属性透传
        if name not in _SAFE_METHODS:
            raise AttributeError(f"禁止访问未授权方法: {name}")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("禁止修改 SafePlaywright 属性")

    # ═══════════════════════════════════════════
    # 受控 API — 与 AI Prompt 的 Action 映射一一对应
    # ═══════════════════════════════════════════

    async def goto(self, url: str) -> None:
        """导航到指定 URL（对应 navigate）"""
        page = object.__getattribute__(self, "_page")
        await page.goto(url)

    async def click(self, selector: str) -> None:
        """点击元素（对应 click）"""
        page = object.__getattribute__(self, "_page")
        await page.locator(selector).click()

    async def fill(self, selector: str, value: str) -> None:
        """填充输入框（对应 fill）"""
        page = object.__getattribute__(self, "_page")
        await page.locator(selector).fill(value)

    async def select(self, selector: str, value: str) -> None:
        """下拉选择（对应 select）"""
        page = object.__getattribute__(self, "_page")
        await page.locator(selector).select_option(value)

    async def hover(self, selector: str) -> None:
        """悬停元素（对应 hover）"""
        page = object.__getattribute__(self, "_page")
        await page.locator(selector).hover()

    async def assert_text(self, selector: str, expected: str) -> None:
        """断言元素包含文本（对应 assert_text）"""
        from playwright.async_api import expect
        page = object.__getattribute__(self, "_page")
        await expect(page.locator(selector)).to_contain_text(expected)

    async def assert_visible(self, selector: str) -> None:
        """断言元素可见（对应 assert_visible）"""
        from playwright.async_api import expect
        page = object.__getattribute__(self, "_page")
        await expect(page.locator(selector)).to_be_visible()

    async def screenshot(self, path: str = "") -> None:
        """截图（对应 screenshot）"""
        page = object.__getattribute__(self, "_page")
        kwargs: dict[str, Any] = {"full_page": True}
        if path:
            kwargs["path"] = path
        await page.screenshot(**kwargs)

    async def wait(self, timeout_ms: int) -> None:
        """等待指定毫秒数（对应 wait）"""
        page = object.__getattribute__(self, "_page")
        await page.wait_for_timeout(timeout_ms)
