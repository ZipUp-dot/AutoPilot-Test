"""P1-3 验收：Mock 代码在新 Validator + 新 namespace 下正常生成与执行

验收 3/7 核心：ai_service 的 Mock 模板已去掉一切外部依赖
（import asyncio/json、from datetime 等），耗时/统计由 monitor 钩子采集。
修后 Mock 代码（Web/Android）必须：
  - 在新 Validator 下 is_valid=1（validate 返回 None）
  - 在新 namespace 下正常执行（Web: SafePlaywright + run_test(safe)；
    Android: DriverProxy + run_test(driver)）
  - 不包含 import / datetime / reports/ 字样
  - screenshot 动作走受控路径解析，不产生 uploads/uploads/ 叠加
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ai_service import _mock_web_code, _mock_android_code
from app.services.playwright_service import _build_namespace
from app.services.appium_service import _build_sync_namespace
from app.utils.code_validator import CodeValidator
from app.utils.safe_playwright import SafePlaywright
from app.utils.appium_proxy import DriverProxy

_STEPS = json.dumps([
    {"step_number": 1, "action": "click", "target": "登录按钮",
     "value": "", "description": "点击登录"},
    {"step_number": 2, "action": "screenshot", "target": "",
     "value": "", "description": "截图"},
], ensure_ascii=False)


class TestMockValidator:
    """Mock 代码在新 Validator 下 is_valid=1"""

    def test_web_mock_passes_validator(self):
        code = _mock_web_code("https://example.com", _STEPS)
        assert CodeValidator.validate(code, platform="web") is None

    def test_android_mock_passes_validator(self):
        code = _mock_android_code(_STEPS)
        assert CodeValidator.validate(code, platform="android") is None

    def test_web_mock_no_external_dependencies(self):
        code = _mock_web_code("https://example.com", _STEPS)
        assert "import" not in code
        assert "datetime" not in code
        assert "reports/" not in code
        assert "start_time" not in code
        assert "duration" not in code

    def test_android_mock_no_external_dependencies(self):
        code = _mock_android_code(_STEPS)
        assert "import" not in code
        assert "datetime" not in code
        assert "reports/" not in code
        assert "start_time" not in code
        assert "duration" not in code


class TestMockExecution:
    """Mock 代码在新 namespace 下正常执行"""

    def test_web_mock_executes_under_new_namespace(self):
        page = AsyncMock()
        # Playwright 的 page.locator() 是同步方法返回 Locator，
        # 必须用同步 MagicMock 模拟（AsyncMock 会返回协程导致 .click() 崩溃）
        locator = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        safe = SafePlaywright(page)
        ns = _build_namespace(safe, MagicMock())
        code = _mock_web_code("https://example.com", _STEPS)
        assert CodeValidator.validate(code, platform="web") is None

        exec(code, ns)
        result = asyncio.run(ns["run_test"](ns["safe"]))

        assert result["success"] is True
        assert len(result["steps"]) == 2
        # 截图动作走受控路径解析：SCREENSHOT_DIR/step_2.png，无 uploads/uploads/ 叠加
        kwargs = page.screenshot.await_args.kwargs
        assert kwargs["path"].endswith("step_2.png")
        assert "uploads/uploads" not in kwargs["path"]

    def test_android_mock_executes_under_new_namespace(self):
        driver = MagicMock()
        ns = _build_sync_namespace(driver, MagicMock())
        code = _mock_android_code(_STEPS)
        assert CodeValidator.validate(code, platform="android") is None

        exec(code, ns)
        # run_test 收到的是受控 DriverProxy（形参遮蔽修复）
        result = ns["run_test"](ns["driver"])

        assert result["success"] is True
        assert len(result["steps"]) == 2
        # find_element 经 ElementProxy 透传原生 mock
        assert driver.find_element.call_args is not None
        # 截图走受控实现：AI 路径 → SCREENSHOT_DIR 绝对路径，无叠加
        assert driver.save_screenshot.call_args is not None
        resolved = driver.save_screenshot.call_args.args[0]
        assert resolved.endswith("step_2.png")
        assert "uploads/uploads" not in resolved

    def test_namespace_driver_is_proxy(self):
        ns = _build_sync_namespace(MagicMock(), MagicMock())
        assert isinstance(ns["driver"], DriverProxy)
