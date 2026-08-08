"""Code injector + validator unit tests"""
import sys
sys.path.insert(0, ".")

from app.utils.code_validator import CodeValidator
from app.utils.code_injector import CodeInjector
from app.exceptions import SecurityException

# ── Test 1: Validator blocks dangerous code ──
print("=== CodeValidator ===")

# Block import os
err = CodeValidator.validate("import os\nasync def run_test(page):\n    return {'success': True}")
print(f"1. import os blocked: {'PASS' if err and 'os' in err else 'FAIL'} ({err})")

# Block eval
err = CodeValidator.validate("async def run_test(page):\n    eval('1+1')\n    return {'success': True}")
print(f"2. eval blocked: {'PASS' if err and 'eval' in err else 'FAIL'} ({err})")

# Block __import__
err = CodeValidator.validate("async def run_test(page):\n    __import__('os')\n    return {'success': True}")
print(f"3. __import__ blocked: {'PASS' if err and '__import__' in err else 'FAIL'} ({err})")

# Valid code passes
err = CodeValidator.validate("from playwright.async_api import Page\nasync def run_test(page):\n    return {'success': True}")
print(f"4. valid code: {'PASS' if err is None else 'FAIL'} ({err})")

# Missing run_test
err = CodeValidator.validate("print('hello')")
print(f"5. missing run_test: {'PASS' if err and 'run_test' in err else 'FAIL'} ({err})")

# Syntax error
err = CodeValidator.validate("async def run_test(page)\n    return")
print(f"6. syntax error: {'PASS' if err and '语法' in err else 'FAIL'} ({err})")

# ── Test 2: Injector wraps Playwright operations ──
print("\n=== CodeInjector ===")

mock_code = '''from playwright.async_api import Page, expect
import asyncio
from datetime import datetime


async def run_test(page: Page) -> dict:
    steps_result = []
    start_time = datetime.now()
    try:
        print("[执行] Mock 测试")
        await page.goto("https://example.com")
        await page.wait_for_load_state("networkidle")

        screenshot_path = "reports/screenshots/mock_test.png"
        await page.screenshot(path=screenshot_path, full_page=True)

        await page.locator("#username").fill("admin")
        await page.locator("#password").fill("123456")
        await page.locator("button[type=submit]").click()

        await expect(page.locator(".welcome")).to_contain_text("欢迎")

        steps_result.append({"step": 1, "status": "passed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}

    duration = (datetime.now() - start_time).total_seconds()
    return {"success": True, "message": f"ok {duration}s", "steps": steps_result}
'''

try:
    injected = CodeInjector.inject(mock_code)
    # Check for injection markers
    checks = [
        ("__monitor_before" in injected, "has __monitor_before"),
        ("__monitor_after" in injected, "has __monitor_after"),
        ("try:" in injected, "has try block"),
        ("except Exception as __ae:" in injected, "has except handler"),
        ("async def run_test" in injected, "has run_test"),
        ("page.goto" in injected, "preserves page.goto"),
        ("page.locator" in injected, "preserves page.locator"),
        ("expect(page.locator" in injected, "preserves expect"),
        ("page.screenshot" in injected, "preserves page.screenshot"),
    ]
    all_pass = True
    for ok, label in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: {label}")

    if all_pass:
        print("Injector: ALL CHECKS PASSED")
    else:
        print("Injector: SOME CHECKS FAILED")

    # Verify the injected code is valid Python
    import ast
    try:
        ast.parse(injected)
        print("Injector: ast.parse PASS")
    except SyntaxError as e:
        print(f"Injector: ast.parse FAIL — {e}")
        print("--- Injected code ---")
        print(injected[:1000])

except SecurityException as e:
    print(f"Injector FAIL: {e}")

print("\n=== ALL TESTS DONE ===")
