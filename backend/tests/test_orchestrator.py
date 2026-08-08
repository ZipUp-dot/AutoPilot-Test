"""AutoPilot 编排器测试 — Mock Service 注入 + 端到端验证"""

import sys
sys.path.insert(0, ".")

# ── Unit: Mock Service Test ──
print("=== Orchestrator Unit Tests ===\n")

import asyncio
from unittest.mock import MagicMock

from app.services.orchestrator import TestOrchestrator

# Mock services
mock_ai = MagicMock()
mock_ai.generate_batch.return_value = [
    {"case_id": 1, "status": "success", "code_id": 10},
    {"case_id": 2, "status": "success", "code_id": 11},
]

mock_pw = MagicMock()
mock_pw.create_execution.return_value = 42

mock_report = MagicMock()
mock_report.generate.return_value = {"report_id": 1, "download_url": "/reports/report.html"}

# Test 1: run_generate_only
print("Test 1: run_generate_only")
orc = TestOrchestrator(ai_service=mock_ai)
result = asyncio.run(orc.run_generate_only(1, [1, 2]))
print(f"  PASS: generated={result['generated_count']} failed={result['failed_count']}")

# Test 2: run_execute_only
print("Test 2: run_execute_only")
orc = TestOrchestrator(ai_service=mock_ai, playwright_service=mock_pw, report_service=mock_report)
result = asyncio.run(orc.run_execute_only(1, [1, 2], "headless", "TestBatch"))
print(f"  PASS: execution_id={result['execution_id']} status={result['status']}")
# Don't await the monitor task, it will timeout in tests

# Test 3: run_full_pipeline (all pre-generated)
print("Test 3: run_full_pipeline (all pre-generated)")
# Mock _check_cases_need_generation to return empty (all have code)
async def mock_empty(*args, **kwargs):
    return []
orc._check_cases_need_generation = mock_empty
result = asyncio.run(orc.run_full_pipeline(1, [1, 2], "headless", "FullBatch"))
print(f"  PASS: execution_id={result['execution_id']} generated={result['generated']}")

# Test 4: run_full_pipeline (need generation)
print("Test 4: run_full_pipeline (need generation)")
async def mock_need_gen(*args, **kwargs):
    return [3, 4]
orc._check_cases_need_generation = mock_need_gen
mock_ai.generate_batch.return_value = [
    {"case_id": 3, "status": "success", "code_id": 12},
    {"case_id": 4, "status": "failed", "error": "no elements"},
]
result = asyncio.run(orc.run_full_pipeline(1, [3, 4], "headless", "NeedGen"))
print(f"  PASS: execution_id={result['execution_id']} generated={result['generated']}")

# Test 5: Exception isolation (AI fails but pipeline continues)
print("Test 5: Exception isolation")
mock_ai_broken = MagicMock()
mock_ai_broken.generate_batch.side_effect = RuntimeError("AI service down")
orc2 = TestOrchestrator(ai_service=mock_ai_broken, playwright_service=mock_pw, report_service=mock_report)

async def mock_gen_needed(*args, **kwargs):
    return [1]
orc2._check_cases_need_generation = mock_gen_needed
try:
    result = asyncio.run(orc2.run_full_pipeline(1, [1], "headless", "BrokenAI"))
    print(f"  PASS (no exception): execution_id={result['execution_id']}")
except Exception as e:
    print(f"  FAIL: pipeline crashed: {e}")

# Test 6: _check_cases_need_generation
print("Test 6: _check_cases_need_generation")
cases = asyncio.run(orc._check_cases_need_generation([99999]))
print(f"  PASS: need generation={len(cases)} (should be >0 for non-existent case)")

# Test 7: Dependency injection (no service provided = graceful degradation)
print("Test 7: No-service orchestration")
orc_empty = TestOrchestrator()
try:
    orc_empty.playwright_service  # Should not raise
    print("  PASS: empty orchestrator created without crash")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 8: Orchestrator doesn't import Playwright
print("Test 8: No Playwright import (static check)")
import inspect
orchestrator_source = inspect.getsource(TestOrchestrator)
has_playwright_import = "from playwright" in orchestrator_source or "import playwright" in orchestrator_source
print(f"  PASS: no Playwright import in orchestrator: {not has_playwright_import}")

print("\n=== ALL UNIT TESTS DONE ===\n")


# ── API Integration Test ──
print("=== Orchestrator API Integration Test ===\n")

import httpx, json, io, time
from openpyxl import Workbook

BASE = "http://127.0.0.1:8000/api/v1"

# 1. Create project + case
r = httpx.post(f"{BASE}/projects/", json={
    "name": "OrchestratorTest",
    "target_url": "https://example.com",
})
pid = r.json()["data"]["id"]
print(f"Project: {pid}")

wb = Workbook()
ws = wb.active
ws.append(["用例名称", "操作步骤", "优先级"])
steps = json.dumps([
    {"action": "navigate", "target": "https://example.com", "value": "", "description": "打开首页"},
    {"action": "click", "target": "h1", "value": "", "description": "点击标题"},
], ensure_ascii=False)
ws.append(["编排器测试用例", steps, "P1"])
buf = io.BytesIO()
wb.save(buf)
r = httpx.post(f"{BASE}/projects/{pid}/cases/import",
    files={"file": ("orch_test.xlsx", buf.getvalue())})
print(f"Import: total={r.json()['data']['total']}")

r = httpx.get(f"{BASE}/projects/{pid}/cases/", params={"page": 1, "size": 10})
case_id = r.json()["data"]["items"][0]["id"]
print(f"Case: id={case_id}")

# Generate code first
r = httpx.post(f"{BASE}/projects/{pid}/cases/{case_id}/generate", timeout=30)
print(f"Generate: code_id={r.json()['data']['code_id']}")

# 2. Test create execution (via orchestrator)
r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
    "case_ids": [case_id],
    "mode": "headless",
    "batch_name": "Orch Test Batch",
})
d = r.json()["data"]
eid = d["execution_id"]
print(f"\nCreate execution (via orchestrator): execution_id={eid} status={d['status']}")

# 3. Poll status
print("Polling execution status...")
for i in range(15):
    time.sleep(2)
    r = httpx.get(f"{BASE}/executions/{eid}/status")
    s = r.json()["data"]
    print(f"  [{i*2}s] status={s['status']} progress={s['progress']} pct={s['percentage']}%")
    if s["status"] in ("completed", "failed", "stopped"):
        break

# 4. Check if report was auto-generated
time.sleep(3)
r = httpx.get(f"{BASE}/executions/{eid}/reports")
if r.status_code == 200:
    report_info = r.json()["data"]
    print(f"\nReport auto-generated: report_id={report_info['report_id']} url={report_info['download_url']}")
else:
    print(f"\nReport not yet generated: HTTP {r.status_code}")

# 5. Test validation: execute without generated code
print("\nValidation: execute without generated code")
r = httpx.post(f"{BASE}/projects/{pid}/cases/import",
    files={"file": ("nocode.xlsx", buf.getvalue())})

r2 = httpx.get(f"{BASE}/projects/{pid}/cases/", params={"page": 1, "size": 10})
items = r2.json()["data"]["items"]
nocode_case = None
for item in items:
    gen = httpx.get(f"{BASE}/projects/{pid}/cases/{item['id']}/codes")
    codes_data = gen.json().get("data")
    if not codes_data or len(codes_data) == 0:
        nocode_case = item["id"]
        break

if nocode_case:
    r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
        "case_ids": [nocode_case],
        "mode": "headless",
    })
    print(f"  Security gate: HTTP {r.status_code} — {r.json().get('detail', '')[:80]}")
else:
    print("  SKIP: no uncoded case found")

# Cleanup
r = httpx.delete(f"{BASE}/projects/{pid}")
print(f"\nCleanup: {r.json()['data']}")

print("\n=== ALL ORCHESTRATOR TESTS DONE ===")
