"""P1-3 验收：双端 namespace 同步收口

验收 1/7 核心：
  - 双端 ALLOWED_BUILTINS 均无裸 __import__
  - 双端 namespace 均无完整标准库模块注入（json/time/asyncio/datetime）
  - Android namespace 注入的是受控 DriverProxy + AppiumBy wrapper
  - DriverProxy.find_element/find_elements 必须返回 ElementProxy（防原生元素逃逸）
  - ElementProxy 白名单方法/属性，递归代理，禁止反射逃逸
"""

from unittest.mock import MagicMock

import pytest

from app.services.playwright_service import (
    ALLOWED_BUILTINS as WEB_ALLOWED_BUILTINS,
    _build_namespace,
)
from app.services.appium_service import (
    ALLOWED_BUILTINS as ANDROID_ALLOWED_BUILTINS,
    _build_sync_namespace,
)
from app.utils.safe_playwright import SafePlaywright
from app.utils.appium_proxy import DriverProxy, ElementProxy, AppiumByProxy

_STDLIB_NAMES = ("json", "time", "asyncio", "datetime")


# ═══════════════════════════════════════════════
# 双端 ALLOWED_BUILTINS：无裸 __import__
# ═══════════════════════════════════════════════

class TestAllowedBuiltinsNoImport:
    """验收1: 双端白名单均无裸 __import__"""

    def test_web_allowed_builtins_no_import(self):
        assert "__import__" not in WEB_ALLOWED_BUILTINS

    def test_android_allowed_builtins_no_import(self):
        assert "__import__" not in ANDROID_ALLOWED_BUILTINS

    def test_web_namespace_builtins_no_import(self):
        ns = _build_namespace(SafePlaywright(MagicMock()), MagicMock())
        builtins = ns["__builtins__"]
        assert "__import__" not in builtins
        assert "eval" not in builtins
        assert "exec" not in builtins

    def test_android_namespace_builtins_no_import(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        builtins = ns["__builtins__"]
        assert "__import__" not in builtins
        assert "eval" not in builtins
        assert "exec" not in builtins


# ═══════════════════════════════════════════════
# 双端 namespace：无完整标准库模块注入
# ═══════════════════════════════════════════════

class TestNamespaceNoStdlibModules:
    """验收1: 双端 namespace 均无完整标准库模块注入"""

    def test_web_namespace_no_stdlib_modules(self):
        ns = _build_namespace(SafePlaywright(MagicMock()), MagicMock())
        for name in _STDLIB_NAMES:
            assert name not in ns, f"Web namespace 不应注入 {name}"

    def test_android_namespace_no_stdlib_modules(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        for name in _STDLIB_NAMES:
            assert name not in ns, f"Android namespace 不应注入 {name}"


# ═══════════════════════════════════════════════
# Android namespace：受控 driver + AppiumBy wrapper
# ═══════════════════════════════════════════════

class TestAndroidNamespaceControlledDriver:
    """验收1: Android driver 受控化 + AppiumBy wrapper"""

    def test_namespace_driver_is_driver_proxy(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        assert isinstance(ns["driver"], DriverProxy)
        assert not isinstance(ns["driver"], MagicMock)

    def test_namespace_appiumby_is_wrapper(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        # namespace 注入的是 AppiumByProxy 类本身（受控 wrapper），非原生 AppiumBy
        assert ns["AppiumBy"] is AppiumByProxy
        assert ns["AppiumBy"].ID == "id"
        assert ns["AppiumBy"].XPATH == "xpath"
        assert ns["AppiumBy"].ACCESSIBILITY_ID == "accessibility id"
        assert ns["AppiumBy"].CLASS_NAME == "class name"

    def test_namespace_has_controlled_sleep(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        assert callable(ns["sleep"])

    def test_namespace_hooks_and_print(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        assert ns["__monitor_before"] is not None
        assert ns["__monitor_after"] is not None
        assert callable(ns["print"])


# ═══════════════════════════════════════════════
# DriverProxy：find_element 返回 ElementProxy + 反射逃逸拦截
# ═══════════════════════════════════════════════

class TestDriverProxyElementProxy:
    """验收1: DriverProxy 返回值钉死为 ElementProxy，禁止反射逃逸"""

    def _make_driver(self):
        driver = MagicMock()
        el = MagicMock()
        el2 = MagicMock()
        driver.find_element.return_value = el
        driver.find_elements.return_value = [el2]
        return driver, el, el2

    def test_find_element_returns_element_proxy(self):
        driver, el, _ = self._make_driver()
        proxy = DriverProxy(driver)
        result = proxy.find_element("id", "btn")
        assert isinstance(result, ElementProxy)
        # 返回值必须 proxy 包装，禁止原生元素对象逃逸
        assert not isinstance(result, MagicMock)
        driver.find_element.assert_called_once_with("id", "btn")

    def test_find_elements_returns_element_proxy_list(self):
        driver, _, el2 = self._make_driver()
        proxy = DriverProxy(driver)
        results = proxy.find_elements("id", "list")
        assert isinstance(results, list)
        assert all(isinstance(r, ElementProxy) for r in results)

    def test_element_proxy_click_passthrough(self):
        driver, el, _ = self._make_driver()
        proxy = DriverProxy(driver)
        proxy.find_element("id", "btn").click()
        el.click.assert_called_once_with()

    def test_element_proxy_send_keys_passthrough(self):
        driver, el, _ = self._make_driver()
        proxy = DriverProxy(driver)
        proxy.find_element("id", "input").send_keys("admin")
        el.send_keys.assert_called_once_with("admin")

    def test_element_proxy_get_attribute_returns_value(self):
        driver, el, _ = self._make_driver()
        el.get_attribute.return_value = "text-content"
        proxy = DriverProxy(driver)
        assert proxy.find_element("id", "btn").get_attribute("text") == "text-content"
        el.get_attribute.assert_called_once_with("text")

    def test_element_proxy_recursive_find(self):
        """递归代理：元素再 find_element 仍返回 ElementProxy"""
        driver, el, _ = self._make_driver()
        child = MagicMock()
        el.find_element.return_value = child
        proxy = DriverProxy(driver)
        result = proxy.find_element("id", "parent").find_element("id", "child")
        assert isinstance(result, ElementProxy)
        el.find_element.assert_called_once_with("id", "child")

    def test_element_proxy_properties_data_only(self):
        driver, el, _ = self._make_driver()
        el.text = "Hello"
        el.size = {"width": 100, "height": 50}
        proxy = DriverProxy(driver)
        elem = proxy.find_element("id", "btn")
        assert elem.text == "Hello"
        assert elem.size == {"width": 100, "height": 50}

    def test_driver_proxy_reflection_escape_blocked(self):
        driver = MagicMock()
        proxy = DriverProxy(driver)
        # __class__ / __dict__ / __getattribute__ / _inner 全部拦截
        for attr in ("__class__", "__dict__", "__getattribute__", "_inner"):
            with pytest.raises(AttributeError):
                getattr(proxy, attr)

    def test_driver_proxy_unknown_method_blocked(self):
        driver = MagicMock()
        proxy = DriverProxy(driver)
        with pytest.raises(AttributeError):
            proxy.execute_script("return 1")

    def test_element_proxy_reflection_escape_blocked(self):
        driver, el, _ = self._make_driver()
        elem = DriverProxy(driver).find_element("id", "btn")
        for attr in ("__class__", "__dict__", "_inner", "parent"):
            with pytest.raises(AttributeError):
                getattr(elem, attr)
