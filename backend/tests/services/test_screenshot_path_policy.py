"""P1-3 验收：截图路径口径（uploads/screenshots/ 子树 + 越界 SecurityError）

验收 4：
  - 写 uploads/screenshots/... 成功
  - 写 ../etc 与 reports/ 均被拒（SecurityError）
  - 带前缀路径解析后不产生 uploads/uploads/ 叠加
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.exceptions import SecurityError
from app.utils.screenshot_policy import ScreenshotPathPolicy
from app.utils.appium_proxy import DriverProxy


class TestScreenshotPathPolicyResolve:
    """resolve_ai_path 正常解析"""

    def test_resolve_uploads_screenshots_ok(self):
        resolved = ScreenshotPathPolicy.resolve_ai_path("uploads/screenshots/step_1.png")
        expected = str(Path(settings.SCREENSHOT_DIR).resolve() / "step_1.png")
        assert resolved == expected
        # 落在 SCREENSHOT_DIR 内
        assert Path(resolved).is_relative_to(Path(settings.SCREENSHOT_DIR).resolve())

    def test_resolve_nested_relative_subpath(self):
        resolved = ScreenshotPathPolicy.resolve_ai_path(
            "uploads/screenshots/exec_5/case_2/a.png"
        )
        expected = str(
            Path(settings.SCREENSHOT_DIR).resolve() / "exec_5" / "case_2" / "a.png"
        )
        assert resolved == expected

    def test_no_uploads_uploads_stacking(self):
        """带前缀路径不再叠加到 uploads/ 下（不会产生 uploads/uploads/...）"""
        resolved = ScreenshotPathPolicy.resolve_ai_path("uploads/screenshots/a.png")
        norm = Path(resolved).as_posix()
        assert "/uploads/uploads/" not in norm + "/"
        assert norm.endswith("/a.png")

    def test_empty_path_returns_empty(self):
        assert ScreenshotPathPolicy.resolve_ai_path("") == ""

    def test_backslash_normalized(self):
        resolved = ScreenshotPathPolicy.resolve_ai_path(
            "uploads\\screenshots\\step_1.png"
        )
        assert resolved == str(Path(settings.SCREENSHOT_DIR).resolve() / "step_1.png")


class TestScreenshotPathPolicyRejects:
    """resolve_ai_path 越界拒绝"""

    def test_rejects_reports_path(self):
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("reports/screenshots/a.png")

    def test_rejects_uploads_uploads_path(self):
        """uploads/uploads/... 不是截图子树 → 拒绝"""
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("uploads/uploads/a.png")

    def test_rejects_parent_traversal(self):
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("uploads/screenshots/../etc/a.png")
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("uploads/screenshots/../../etc")

    def test_rejects_absolute_path(self):
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("/etc/passwd")
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("C:\\windows\\system32")

    def test_rejects_bare_path(self):
        with pytest.raises(SecurityError):
            ScreenshotPathPolicy.resolve_ai_path("a.png")


class TestIsScreenshotPath:
    """is_screenshot_path 口径校验"""

    def test_true_for_valid_prefix(self):
        assert ScreenshotPathPolicy.is_screenshot_path("uploads/screenshots/a.png") is True

    def test_false_for_others(self):
        for bad in ("reports/a.png", "uploads/uploads/a.png", "a.png", "", "/tmp/x.png"):
            assert ScreenshotPathPolicy.is_screenshot_path(bad) is False


class TestDriverProxySaveScreenshot:
    """DriverProxy.save_screenshot 走截图路径策略"""

    def _driver(self):
        driver = MagicMock()
        driver.save_screenshot.return_value = True
        return driver

    def test_valid_path_delegates_resolved(self):
        from unittest.mock import MagicMock
        driver = self._driver()
        proxy = DriverProxy(driver)
        proxy.save_screenshot("uploads/screenshots/step_1.png")
        expected = str(Path(settings.SCREENSHOT_DIR).resolve() / "step_1.png")
        driver.save_screenshot.assert_called_once_with(expected)

    def test_invalid_path_raises(self):
        from unittest.mock import MagicMock
        driver = self._driver()
        proxy = DriverProxy(driver)
        for bad in ("reports/a.png", "../etc/a.png", "uploads/uploads/a.png"):
            with pytest.raises(SecurityError):
                proxy.save_screenshot(bad)
        driver.save_screenshot.assert_not_called()

    def test_empty_path_raises(self):
        from unittest.mock import MagicMock
        driver = self._driver()
        proxy = DriverProxy(driver)
        with pytest.raises(SecurityError):
            proxy.save_screenshot("")
