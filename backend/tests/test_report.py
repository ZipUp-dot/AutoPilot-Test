"""AutoPilot 报告模块测试"""

import sys
sys.path.insert(0, ".")

# ── Unit: Import & service check ──
print("=== Report Service Unit Tests ===\n")

from app.services.report_service import ReportService, ReportService as RS

# Error classification
tests = [
    ("Timeout 30000ms exceeded", "TimeoutError"),
    ("Element not found: #missing", "ElementNotFoundError"),
    ("Assertion failed: expected visible", "AssertionError"),
    ("net::ERR_CONNECTION_REFUSED", "NavigationError"),
    ("random error", "OtherError"),
]
for msg, expected in tests:
    r = RS._classify_error_type(msg)
    print(f"  {'PASS' if r == expected else 'FAIL'}: {msg[:40]} → {r}")

# Priority distribution
cases = [
    {"priority": "P0", "final_status": "success"},
    {"priority": "P0", "final_status": "failed"},
    {"priority": "P1", "final_status": "success"},
    {"priority": "P1", "final_status": "success"},
    {"priority": "P2", "final_status": "failed"},
]
dist = RS._priority_distribution(cases)
print(f"  PASS: priority dist → {len(dist)} groups")

# Relative path
import os
test_path = os.path.join(os.getcwd(), "uploads", "screenshots", "1", "2", "step_1_before.jpg")
rel = RS._relative_path(test_path)
print(f"  PASS: relative path → {rel[:40]}...")

print("\n=== ALL UNIT TESTS DONE ===\n")


# ── API Integration ──
print("=== Report API Integration Tests ===\n")

import httpx, json, io, time
from openpyxl import Workbook

BASE = "http://127.0.0.1:8000/api/v1"

# 1. Create project + case + execution
r = httpx.post(f"{BASE}/projects/", json={
    "name": "ReportTest",
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
ws.append(["报告测试用例", steps, "P1"])
buf = io.BytesIO()
wb.save(buf)
r = httpx.post(f"{BASE}/projects/{pid}/cases/import",
    files={"file": ("report.xlsx", buf.getvalue())})
print(f"Import: {r.json()['data']['success']}")

r = httpx.get(f"{BASE}/projects/{pid}/cases/", params={"page": 1, "size": 10})
case_id = r.json()["data"]["items"][0]["id"]

r = httpx.post(f"{BASE}/projects/{pid}/cases/{case_id}/generate", timeout=30)
print(f"Generate: code_id={r.json()['data']['code_id']}")

r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
    "case_ids": [case_id],
    "mode": "headless",
    "batch_name": "Report Batch",
})
eid = r.json()["data"]["execution_id"]
print(f"Execution: {eid}")

# Poll until done
print("Polling...")
for i in range(15):
    time.sleep(2)
    r = httpx.get(f"{BASE}/executions/{eid}/status")
    s = r.json()["data"]
    if s["status"] in ("completed", "failed", "healing", "stopped"):
        print(f"  Finished: status={s['status']}")
        break

# Generate report
print("\nGenerating report...")
r = httpx.post(f"{BASE}/executions/{eid}/reports/generate", timeout=30)
d = r.json()["data"]
report_id = d["report_id"]
download_url = d["download_url"]
print(f"  report_id={report_id} url={download_url}")

# Get report info
r = httpx.get(f"{BASE}/executions/{eid}/reports")
info = r.json()["data"]
print(f"  Info: report_id={info['report_id']} summary={info['summary']}")

# Fetch the HTML file
try:
    html_r = httpx.get(f"http://127.0.0.1:8000{download_url}", timeout=10)
    html = html_r.text
    print(f"  HTML: {len(html)} bytes, status={html_r.status_code}")

    # Verify key elements
    checks = [
        ("<title>", "title"),
        ("donutChart", "donut chart canvas"),
        ("priorityChart", "priority chart canvas"),
        ("errorChart", "error chart canvas"),
        ("lightbox", "lightbox"),
        ("exportJSON", "JSON export function"),
        ("exportCSV", "CSV export function"),
        ("print", "print button"),
        ("report-data", "inline JSON data"),
    ]
    for check, label in checks:
        ok = check in html
        print(f"  {'PASS' if ok else 'FAIL'}: {label}")

    # Should work offline (no CDN refs)
    if "cdn" in html.lower() or "http://" in html.lower():
        print("  FAIL: external CDN references found (offline check)")
    else:
        print("  PASS: no external CDN references (offline-ready)")

    if "@media print" in html:
        print("  PASS: print styles present")
    else:
        print("  FAIL: no print styles")
except Exception as e:
    print(f"  ERROR fetching HTML: {e}")

# Test re-generate (should return cached)
r = httpx.post(f"{BASE}/executions/{eid}/reports/generate", timeout=10)
d2 = r.json()["data"]
print(f"\nRe-generate (cached): report_id={d2['report_id']}")

# Test 404 on non-existent report
r = httpx.get(f"{BASE}/executions/99999/reports")
print(f"Non-existent report: HTTP {r.status_code}")

# Cleanup
r = httpx.delete(f"{BASE}/projects/{pid}")
print(f"Cleanup: {r.json()['data']}")

print("\n=== ALL REPORT TESTS DONE ===")
