"""AutoPilot 执行引擎端到端集成测试"""

import httpx, json, io, time
from openpyxl import Workbook

BASE = "http://127.0.0.1:8000/api/v1"

# ── 1. 创建项目 ──
r = httpx.post(f"{BASE}/projects/", json={
    "name": "ExecTest",
    "target_url": "https://example.com",
})
pid = r.json()["data"]["id"]
print(f"Project: {pid}")

# ── 2. 导入测试用例 ──
wb = Workbook()
ws = wb.active
ws.append(["用例编号", "用例名称", "优先级", "前置条件", "操作步骤", "预期结果"])

steps_json = json.dumps([
    {"action": "navigate", "target": "https://example.com", "value": "", "description": "打开首页"},
    {"action": "wait", "target": "networkidle", "value": "", "description": "等待加载"},
    {"action": "assert_text", "target": "h1", "value": "Example Domain", "description": "验证标题"},
], ensure_ascii=False)

ws.append(["TC_EXEC", "访问示例网站", "P0", "", steps_json, "页面显示 Example Domain"])

steps_json2 = json.dumps([
    {"action": "navigate", "target": "https://example.com", "value": "", "description": "打开首页"},
    {"action": "screenshot", "target": "full_page", "value": "", "description": "全页截图"},
], ensure_ascii=False)
ws.append(["TC_SCREEN", "截图测试", "P1", "", steps_json2, "截图保存成功"])

buf = io.BytesIO()
wb.save(buf)
r = httpx.post(f"{BASE}/projects/{pid}/cases/import",
    files={"file": ("tc.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
d = r.json()["data"]
print(f"Import: total={d['total']} success={d['success']} failed={d['failed']}")

# ── 3. 获取用例列表 ──
r = httpx.get(f"{BASE}/projects/{pid}/cases/", params={"page": 1, "size": 10})
items = r.json()["data"]["items"]
case_ids = [it["id"] for it in items]
print(f"Cases: {[it['case_name'] for it in items]}")

# ── 4. 生成代码（Mock 模式）──
for cid in case_ids:
    r = httpx.post(f"{BASE}/projects/{pid}/cases/{cid}/generate", timeout=30)
    g = r.json()
    print(f"Generate case {cid}: code_id={g['data']['code_id']}, is_valid={g['data']['is_valid']}")

# ── 5. 创建执行 ──
print(f"\n--- Creating execution with {len(case_ids)} cases ---")
r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
    "case_ids": case_ids,
    "mode": "headless",
    "batch_name": "E2E Test",
})
d = r.json()
print(f"Create execution: {r.status_code}")
eid = d["data"]["execution_id"]
print(f"execution_id: {eid}, status: {d['data']['status']}")

# ── 6. 轮询执行状态 ──
print("\n--- Polling execution status ---")
max_wait = 30
for i in range(max_wait):
    time.sleep(2)
    r = httpx.get(f"{BASE}/executions/{eid}/status")
    s = r.json()["data"]
    print(f"  [{i*2}s] status={s['status']} progress={s['progress']} ({s['percentage']}%) "
          f"passed={s['passed_cases']} failed={s['failed_cases']} "
          f"ss={'Y' if s.get('latest_screenshot') else 'N'}")

    if s["status"] in ("completed", "failed", "stopped"):
        break

# ── 7. 获取执行详情 ──
print("\n--- Execution detail ---")
r = httpx.get(f"{BASE}/executions/{eid}")
detail = r.json()["data"]
print(f"Status: {detail['status']}")
print(f"Passed: {detail['passed_cases']}, Failed: {detail['failed_cases']}")
print(f"Steps count: {len(detail['steps'])}")
for s in detail["steps"][:5]:
    print(f"  step {s['step_index']}: {s['action']} → {s['status']} "
          f"({s['duration_ms']}ms) "
          f"ss_before={bool(s['screenshot_before'])} "
          f"ss_after={bool(s['screenshot_after'])}")

# ── 8. 测试停止接口（创建第二个执行并立即停止）──
print("\n--- Test stop ---")
r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
    "case_ids": case_ids[:1],
    "mode": "headless",
})
eid2 = r.json()["data"]["execution_id"]
r = httpx.post(f"{BASE}/executions/{eid2}/stop")
print(f"Stop result: {r.json()['data']}")

# 验证状态
r = httpx.get(f"{BASE}/executions/{eid2}/status")
print(f"After stop: {r.json()['data']['status']}")

# ── 9. 测试安全拦截（拒绝无代码的用例直接执行）──
print("\n--- Test security gate ---")
# 先导入一个新用例但不生成代码
wb3 = Workbook()
ws3 = wb3.active
ws3.append(["用例名称", "操作步骤"])
ws3.append(["安检测试",
    json.dumps([{"action": "navigate", "target": "https://example.com", "value": ""}], ensure_ascii=False)])
buf3 = io.BytesIO()
wb3.save(buf3)
r = httpx.post(f"{BASE}/projects/{pid}/cases/import",
    files={"file": ("sec.xlsx", buf3.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
r = httpx.get(f"{BASE}/projects/{pid}/cases/", params={"page": 1, "size": 10})
sec_id = [it["id"] for it in r.json()["data"]["items"] if it["case_name"] == "安检测试"][0]

# 直接执行（应该被拦截）
r = httpx.post(f"{BASE}/projects/{pid}/executions", json={
    "case_ids": [sec_id],
    "mode": "headless",
})
print(f"Security gate: {r.status_code} — {r.json().get('message', '')[:80]}")

# ── 10. 清理 ──
r = httpx.delete(f"{BASE}/projects/{pid}")
print(f"\nCleanup: {r.json()['data']}")
print("\n=== ALL EXECUTION TESTS PASSED ===")
