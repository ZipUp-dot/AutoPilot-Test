"""Appium 受控代理 — DriverProxy / ElementProxy / AppiumByProxy

namespace 注入的 driver 必须为受控 proxy：
  - find_element / find_elements / back / swipe / save_screenshot 白名单透传
  - save_screenshot 重写为走 ScreenshotPathPolicy 的受控实现（路径限 uploads/screenshots/）
  - find_element / find_elements 必须返回 ElementProxy，禁止原生 WebElement 逃逸
  - ElementProxy 采用白名单方法/属性，返回对象继续 proxy 包装（递归代理）
  - 禁止通过 getattr / __dict__ / __class__ 等反射路径逃逸到原生对象
"""

from app.exceptions import SecurityError
from app.utils.screenshot_policy import ScreenshotPathPolicy


class AppiumByProxy:
    """AppiumBy 受控 wrapper — 只暴露定位策略常量，禁止反射访问"""

    __slots__ = ()

    ID = "id"
    XPATH = "xpath"
    ACCESSIBILITY_ID = "accessibility id"
    CLASS_NAME = "class name"
    NAME = "name"
    TEXT = "text"
    UIAUTOMATOR = "-android uiautomator"

    def __getattribute__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(f"禁止访问私有/魔法属性: {name}")
        return object.__getattribute__(self, name)


class ElementProxy:
    """受控元素代理 — 白名单方法/属性，返回值递归代理，禁止反射逃逸

    设计要点：
      - __slots__ 禁止动态加属性
      - __getattribute__ 拦截所有下划线开头属性（含 __class__ / __dict__ / _inner）
      - 白名单方法透传真实元素；find_element/find_elements 返回 ElementProxy
      - 属性（text/location/size/rect）返回纯数据对象
      - 不暴露原生元素的 parent / screenshot 等未开放能力
    """

    __slots__ = ("_inner",)

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattribute__(self, name: str):
        # 反射入口一律拒绝：__class__ / __dict__ / _inner 等
        if name.startswith("_"):
            raise AttributeError(f"禁止访问私有/魔法属性: {name}")
        return object.__getattribute__(self, name)

    # ── 白名单方法 ──
    def click(self):
        return object.__getattribute__(self, "_inner").click()

    def send_keys(self, *args, **kwargs):
        return object.__getattribute__(self, "_inner").send_keys(*args, **kwargs)

    def clear(self):
        return object.__getattribute__(self, "_inner").clear()

    def get_attribute(self, name: str):
        return object.__getattribute__(self, "_inner").get_attribute(name)

    def is_displayed(self):
        return object.__getattribute__(self, "_inner").is_displayed()

    def is_enabled(self):
        return object.__getattribute__(self, "_inner").is_enabled()

    def is_selected(self):
        return object.__getattribute__(self, "_inner").is_selected()

    # ── 递归代理：返回的元素对象继续包装 ──
    def find_element(self, by, value):
        el = object.__getattribute__(self, "_inner").find_element(by, value)
        return ElementProxy(el)

    def find_elements(self, by, value):
        els = object.__getattribute__(self, "_inner").find_elements(by, value)
        return [ElementProxy(e) for e in els]

    # ── 白名单属性（纯数据）──
    @property
    def text(self):
        return object.__getattribute__(self, "_inner").text

    @property
    def location(self):
        return object.__getattribute__(self, "_inner").location

    @property
    def size(self):
        return object.__getattribute__(self, "_inner").size

    @property
    def rect(self):
        return object.__getattribute__(self, "_inner").rect


class DriverProxy:
    """受控 Appium driver 代理

    白名单：find_element / find_elements / back / swipe / save_screenshot
      - find_element / find_elements → ElementProxy（禁止原生元素逃逸）
      - save_screenshot → ScreenshotPathPolicy 受控实现（路径限 uploads/screenshots/）
    """

    __slots__ = ("_inner",)

    _ALLOWED = frozenset({
        "find_element", "find_elements", "back", "swipe", "save_screenshot",
    })

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattribute__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(f"禁止访问私有/魔法属性: {name}")
        # 注意：不能写 self._ALLOWED（会再次触发 __getattribute__ 被拦截），
        # 必须绕过自定义 __getattribute__ 读取白名单
        allowed = object.__getattribute__(self, "_ALLOWED")
        if name not in allowed:
            raise AttributeError(f"禁止访问未授权 driver 方法: {name}")
        return object.__getattribute__(self, name)

    def find_element(self, by, value):
        el = object.__getattribute__(self, "_inner").find_element(by, value)
        return ElementProxy(el)

    def find_elements(self, by, value):
        els = object.__getattribute__(self, "_inner").find_elements(by, value)
        return [ElementProxy(e) for e in els]

    def back(self):
        return object.__getattribute__(self, "_inner").back()

    def swipe(self, *args):
        return object.__getattribute__(self, "_inner").swipe(*args)

    def save_screenshot(self, path: str):
        """受控截图：AI 侧路径必须位于 uploads/screenshots/ 子树"""
        resolved = ScreenshotPathPolicy.resolve_ai_path(path)
        if not resolved:
            raise SecurityError("截图必须指定路径（uploads/screenshots/ 子树）")
        return object.__getattribute__(self, "_inner").save_screenshot(resolved)
