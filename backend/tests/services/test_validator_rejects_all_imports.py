"""P1-3 验收：CodeValidator 禁所有 import（Web/Android 一律拒绝）

验收 1/7 核心：import os 在【Validator 阶段】即被拒（不是 runtime 才炸）。
双层一致：第一层（Validator）一律拒绝 Import / ImportFrom，
第二层（runtime namespace 无 __import__）为兜底。

覆盖：
  - import os / json / time / requests（黑名单与白名单模块）一律拒绝
  - from datetime import datetime / from playwright.async_api import Page 一律拒绝
  - Web / Android 双平台均拒绝
  - 无 import 的合法 Web / Android 契约代码返回 None
"""

import pytest

from app.utils.code_validator import CodeValidator

VALID_WEB_CODE = '''async def run_test(page) -> dict:
    steps_result = []
    await safe.goto("https://example.com")
    steps_result.append({"step": 1, "status": "passed"})
    return {"success": True, "message": "ok", "steps": steps_result}
'''

VALID_ANDROID_CODE = '''def run_test(driver) -> dict:
    steps_result = []
    try:
        print("[执行] 测试")
        driver.find_element(AppiumBy.ID, "btn").click()
        steps_result.append({"step": 1, "status": "passed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}
    return {"success": True, "message": "测试通过", "steps": steps_result}
'''

# import 语句（在合法契约代码前插入）→ 一律拒绝
BANNED_IMPORTS = [
    ("import os", "os"),
    ("import json", "json"),
    ("import time", "time"),
    ("import requests", "requests"),
    ("import socket", "socket"),
    ("from datetime import datetime", "datetime"),
    ("from playwright.async_api import Page", "Page"),
    ("from app.config import settings", "settings"),
]


class TestValidatorRejectsAllImports:
    """验收1: import 一律在 Validator 阶段拒绝（Web/Android 双平台）"""

    @pytest.mark.parametrize("imp,mod", BANNED_IMPORTS)
    @pytest.mark.parametrize("platform,base", [
        ("web", VALID_WEB_CODE),
        ("android", VALID_ANDROID_CODE),
    ])
    def test_any_import_rejected_at_validator_stage(self, platform, base, imp, mod):
        code = imp + "\n" + base
        result = CodeValidator.validate(code, platform=platform)
        assert result is not None, f"{platform} 平台应拒绝: {imp}"
        assert "禁止导入模块" in result
        assert mod in result, f"错误消息应包含模块名 {mod!r}: {result}"

    def test_valid_web_code_without_import_passes(self):
        assert CodeValidator.validate(VALID_WEB_CODE, platform="web") is None

    def test_valid_android_code_without_import_passes(self):
        assert CodeValidator.validate(VALID_ANDROID_CODE, platform="android") is None

    def test_import_in_middle_of_body_rejected(self):
        """import 出现在函数体内同样拒绝（ast.walk 全量遍历）"""
        code = '''async def run_test(page) -> dict:
    import json
    return {"success": True, "steps": []}
'''
        result = CodeValidator.validate(code, platform="web")
        assert result is not None
        assert "禁止导入模块" in result
        assert "json" in result

    def test_multiline_import_rejected(self):
        code = "import os, sys\n" + VALID_WEB_CODE
        result = CodeValidator.validate(code, platform="web")
        assert result is not None
        assert "os" in result and "sys" in result
