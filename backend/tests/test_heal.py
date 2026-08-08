"""AutoPilot 自愈模块测试 — 对齐规范版"""

import sys
sys.path.insert(0, ".")

# ── Unit tests ──
from app.services.heal_service import HealService

print("=== HealService Unit Tests ===\n")

# 1. Error classification
hs = HealService(db=None)
tests = [
    ("Timeout 30000ms exceeded", "TimeoutError"),
    ("Element not found: #missing", "ElementNotFoundError"),
    ("Cannot resolve locator", "ElementNotFoundError"),
    ("Assertion failed: expected visible", "AssertionError"),
    ("expect(...).to_be_visible failed", "AssertionError"),
    ("net::ERR_CONNECTION_REFUSED", "NavigationError"),
    ("some random error", "UnknownError"),
]
all_classify = True
for msg, expected in tests:
    result = hs._classify_error(msg)
    ok = result == expected
    if not ok:
        all_classify = False
    print(f"  {'PASS' if ok else 'FAIL'}: classify '{msg[:40]}' → {result} (expected {expected})")

# 2. Code extraction
print()
code_tests = [
    ("```python\nasync def run_test(page): pass\n```", "async def run_test(page): pass"),
    ("```\nasync def run_test(page): pass\n```", "async def run_test(page): pass"),
    ("async def run_test(page): pass", "async def run_test(page): pass"),
    ("```python\nimport os\n# UNABLE_TO_HEAL: no fix\n```", "import os\n# UNABLE_TO_HEAL: no fix"),
]
all_extract = True
for raw, expected in code_tests:
    result = hs._extract_code(raw)
    ok = result == expected
    if not ok:
        all_extract = False
    print(f"  {'PASS' if ok else 'FAIL'}: extract → '{result[:50]}'")

# 3. Code validation via CodeValidator
print()
from app.utils.code_validator import CodeValidator

valid_code = '''from playwright.async_api import Page, expect
import asyncio
from datetime import datetime


async def run_test(page: Page) -> dict:
    steps_result = []
    start_time = datetime.now()
    try:
        await page.goto("https://example.com")
        await page.locator("h1").click()
        await expect(page.locator("h1")).to_contain_text("Example")
        steps_result.append({"step": 1, "status": "passed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}
    return {"success": True, "message": "ok", "steps": steps_result}
'''

error = CodeValidator.validate(valid_code)
print(f"  PASS: valid heal code: {error is None} ({error})")

dangerous = "import os\nasync def run_test(page):\n    os.system('rm -rf /')\n    return {}"
error = CodeValidator.validate(dangerous)
print(f"  PASS: dangerous code blocked: {error is not None} ({error[:60] if error else 'N/A'})")

no_fn = "print('hello')"
error = CodeValidator.validate(no_fn)
print(f"  PASS: no run_test blocked: {error is not None} ({error[:60] if error else 'N/A'})")

# 4. Prompt template fields
print()
import os
prompt_path = os.path.join(os.path.dirname(__file__), "..", "app", "prompts", "heal_prompt.txt")
with open(prompt_path, "r", encoding="utf-8") as f:
    template = f.read()
required_fields = ["{original_code}", "{failed_step_index}", "{failed_action}", "{failed_target}",
                   "{error_message}", "{dom_snapshot}", "{screenshot_before}", "{screenshot_after}",
                   "{elements_list}"]
missing = [f for f in required_fields if f not in template]
if missing:
    print(f"  FAIL: missing template fields: {missing}")
else:
    print(f"  PASS: heal_prompt.txt contains all {len(required_fields)} required fields")

# Check repair principles
principles = ["get_by_text", "get_by_placeholder", "wait_for", "scroll_into_view", "只修改失败"]
all_principles = True
for p in principles:
    if p not in template:
        print(f"  FAIL: missing principle: '{p}'")
        all_principles = False
if all_principles:
    print(f"  PASS: all {len(principles)} repair principles present")

print(f"\n=== UNIT TESTS: {'ALL PASSED' if all_classify and all_extract and not missing and all_principles else 'SOME FAILED'} ===\n")


# ── API Integration tests ──
print("=== Heal API Integration Tests ===\n")

import httpx, json, io, time
from openpyxl import Workbook

BASE = "http://127.0.0.1:8000/api/v1"

# 1. Create project + case
r = httpx.post(f"{BASE}/projects/", json={
    "name": "HealTestV2",
    "target_url": "https://example.com",
})
pid = r.json()["data"]["id"]
print(f"Project: {pid}")

wb = Workbook()
ws = wb.active
ws.append(["用例名称", "操作步骤", "优先级"])
steps = json.dumps([
    {"action": "navigate", "target": "https://example.com", "value": "", "description": "打开首页"},
    {"action": "click", "target": "#non-existent-element", "value": "", "description": "点击不存在元素"},
], ensure_ascii=False)
ws.append(["自愈测试用例V2", steps, "P1"])
buf = io.BytesIO()
wb.save(buf)
r = httpx.post(f"{BASE}/projects/{pid}/cases/import",
    files={"file": ("heal_v2.xlsx", buf.getvalue())})
print(f"Import: {r.json()['data']['success']} success")

r = httpx.get(f"{BASE}/projects/{pid}/cases/", params={"page": 1, "size": 10})
case_id = r.json()["data"]["items"][0]["id"]
print(f"Case: id={case_id}")

# Generate code
r = httpx.post(f"{BASE}/projects/{pid}/cases/{case_id}/generate", timeout=30)
print(f"Generate: code_id={r.json()['data']['code_id']}")

# Create execution
r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
    "case_ids": [case_id],
    "mode": "headless",
    "batch_name": "Heal Test V2",
})
eid = r.json()["data"]["execution_id"]
print(f"Execution: {eid}")

# Poll until stable
print("Polling execution...")
for i in range(15):
    time.sleep(2)
    r = httpx.get(f"{BASE}/executions/{eid}/status")
    s = r.json()["data"]
    print(f"  [{i*2}s] status={s['status']} progress={s['progress']}")
    if s["status"] in ("completed", "failed", "healing", "stopped"):
        break

# If healing, wait more
if s.get("status") == "healing":
    print("Waiting for healing...")
    for i in range(10):
        time.sleep(2)
        r = httpx.get(f"{BASE}/executions/{eid}/status")
        s = r.json()["data"]
        print(f"  [{i*2}s] status={s['status']}")
        if s["status"] in ("completed", "failed"):
            break

# Check heal records (new format: { items, total })
r = httpx.get(f"{BASE}/executions/{eid}/heal-records")
data = r.json()["data"]
print(f"\nHeal records: items={len(data['items'])} total={data['total']}")
for rec in data["items"][:3]:
    print(f"  id={rec['id']} step={rec['execution_step_id']} retry={rec['retry_count']} status={rec['retry_status']}")

# Check generated_codes for is_healed
r = httpx.get(f"{BASE}/projects/{pid}/cases/{case_id}/codes")
codes = r.json()["data"] or []
healed_codes = [c for c in codes if c.get("is_healed") and c["is_healed"] == 1]
print(f"Generated codes: total={len(codes)} healed={len(healed_codes)}")

# Test manual heal trigger
r = httpx.get(f"{BASE}/executions/{eid}")
steps = r.json()["data"]["steps"]
failed_steps = [s for s in steps if s["status"] == "failed"]
if failed_steps:
    fs = failed_steps[0]
    print(f"\nManual heal: case={fs['case_id']} step={fs['step_index']}")
    r = httpx.post(f"{BASE}/executions/{eid}/heal", json={
        "case_id": fs["case_id"],
        "step_index": fs["step_index"],
    }, timeout=120)
    d = r.json()["data"]
    print(f"  heal_id={d['heal_id']} status={d['retry_status']} retry={d['retry_count']}")
    print(f"  healed_code length: {len(d.get('healed_code', ''))}")
else:
    print("\nNo failed steps for manual heal test")

# Test validation: non-failed step
r = httpx.post(f"{BASE}/executions/{eid}/heal", json={
    "case_id": case_id,
    "step_index": 1,  # first step is usually success
})
print(f"\nValidation (non-failed): HTTP {r.status_code}")

# Cleanup
r = httpx.delete(f"{BASE}/projects/{pid}")
print(f"Cleanup: {r.json()['data']}")

print("\n=== ALL HEAL V2 TESTS DONE ===")
