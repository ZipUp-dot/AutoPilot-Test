"""AutoPilot 前后端联调测试 — 完整业务链路验证

覆盖:
  1. 项目 CRUD
  2. 元素抓取
  3. Excel 用例导入 + 列表 + 筛选
  4. AI 代码生成（单条 + 批量）
  5. 执行创建 + 状态轮询 + 停止
  6. 报告生成 + 查询
  7. 执行详情 + 步骤截图
  8. 自愈 API
  9. 安全门禁
"""

import sys, json, io, time, os

BASE = "http://127.0.0.1:8000/api/v1"

passed = 0
failed = 0

def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {desc}")
    else:
        failed += 1
        print(f"  [FAIL] {desc} -- {detail}")

def api(method, path, timeout=60, **kw):
    import httpx
    url = f"{BASE}{path}"
    resp = httpx.request(method, url, timeout=timeout, **kw)
    return resp

print("=" * 60)
print("AutoPilot 前后端联调测试")
print("=" * 60)

# ═══════════════════════════════════════════════
# 1. 项目 CRUD
# ═══════════════════════════════════════════════
print("\n--- 1. 项目 CRUD ---")

r = api("POST", "/projects/", json={"name": "联调测试项目", "target_url": "https://example.com"})
check("创建项目", r.status_code == 200, str(r.status_code))
pid = r.json()["data"]["id"]
check("项目ID > 0", pid > 0)

r = api("GET", "/projects/", params={"page": 1, "size": 10})
check("项目列表", r.status_code == 200 and len(r.json()["data"]["items"]) >= 1)

r = api("GET", f"/projects/{pid}")
check("项目详情", r.status_code == 200 and r.json()["data"]["name"] == "联调测试项目")

# 编辑
r = api("PUT", f"/projects/{pid}", json={"name": "联调测试项目-已编辑", "target_url": "https://example.com"})
check("编辑项目", r.status_code == 200, str(r.status_code))

# 表单校验
r = api("POST", "/projects/", json={"name": "", "target_url": "not-a-url"})
check("表单校验-空名称", r.status_code == 422, str(r.status_code))

# ═══════════════════════════════════════════════
# 2. 元素抓取
# ═══════════════════════════════════════════════
print("\n--- 2. 元素抓取 ---")

elements_ok = False
try:
    r = api("POST", f"/projects/{pid}/elements/crawl", json={"max_depth": 1}, timeout=120)
    if r.status_code == 200:
        elements_ok = True
        check("元素抓取", True, f"crawled={r.json().get('data',{}).get('crawled_count','?')}")
    else:
        check("元素抓取", False, f"HTTP {r.status_code}: {r.text[:100]}")
except Exception as e:
    check("元素抓取(超时/网络)", False, str(e)[:80])

if elements_ok:
    r = api("GET", f"/projects/{pid}/elements/", params={"page": 1, "size": 20})
    elements = r.json()["data"]
    check("元素列表", r.status_code == 200, f"total={elements.get('total', 'N/A')}")

    # 搜索筛选
    if elements.get("items"):
        r = api("GET", f"/projects/{pid}/elements/", params={"page": 1, "size": 20, "keyword": "button"})
        check("元素搜索", r.status_code == 200)
else:
    print("  [SKIP] 元素抓取不可用，跳过元素列表/搜索测试")

# ═══════════════════════════════════════════════
# 3. Excel 用例导入
# ═══════════════════════════════════════════════
print("\n--- 3. Excel 用例导入 ---")

from openpyxl import Workbook
wb = Workbook()
ws = wb.active
ws.append(["用例名称", "操作步骤", "优先级", "预期结果"])
steps_json = json.dumps([
    {"action": "navigate", "target": "https://example.com", "value": "", "description": "打开首页"},
    {"action": "wait", "target": "1", "value": "", "description": "等待1秒"},
], ensure_ascii=False)
ws.append(["访问Example", steps_json, "P1", "页面正常加载"])
ws.append(["断言测试", json.dumps([
    {"action": "navigate", "target": "https://example.com", "value": "", "description": "打开首页"},
], ensure_ascii=False), "P0", "页面正常加载"])

buf = io.BytesIO()
wb.save(buf)
buf.seek(0)

r = api("POST", f"/projects/{pid}/cases/import",
    files={"file": ("test_cases.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
check("导入用例", r.status_code == 200, f"data={r.json().get('data')}")

r = api("GET", f"/projects/{pid}/cases/", params={"page": 1, "size": 20})
cases_data = r.json()["data"]
check("用例列表", r.status_code == 200 and len(cases_data.get("items", [])) >= 2,
    f"items={len(cases_data.get('items', []))}")

cases = cases_data["items"]
case_id_1 = cases[0]["id"]
case_id_2 = cases[1]["id"]

# 搜索
r = api("GET", f"/projects/{pid}/cases/", params={"page": 1, "size": 20, "keyword": "Example"})
check("用例搜索", r.status_code == 200 and len(r.json()["data"]["items"]) >= 1)

# 详情
r = api("GET", f"/projects/{pid}/cases/{case_id_1}")
check("用例详情", r.status_code == 200, str(r.status_code))

# ═══════════════════════════════════════════════
# 4. AI 代码生成
# ═══════════════════════════════════════════════
print("\n--- 4. AI 代码生成 ---")

r = api("POST", f"/projects/{pid}/cases/{case_id_1}/generate")
check("单条生成", r.status_code == 200, str(r.status_code))
code_id = r.json()["data"]["code_id"]

r = api("GET", f"/projects/{pid}/cases/{case_id_1}/code")
check("获取代码", r.status_code == 200, f"code_id in response")

# 批量生成
r = api("POST", f"/projects/{pid}/cases/generate-batch", json={"case_ids": [case_id_2]})
check("批量生成", r.status_code == 200, str(r.status_code))

# ═══════════════════════════════════════════════
# 5. 执行引擎
# ═══════════════════════════════════════════════
print("\n--- 5. 执行引擎 ---")

# 安全门禁: 无代码用例
r = api("POST", f"/projects/{pid}/cases/import",
    files={"file": ("nocode.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
r = api("GET", f"/projects/{pid}/cases/", params={"page": 1, "size": 50})
all_cases = r.json()["data"]["items"]
nocode_id = None
for c in all_cases:
    if c.get("status") != "generated":
        nocode_id = c["id"]
        break

if nocode_id:
    r = api("POST", f"/projects/{pid}/executions", json={"case_ids": [nocode_id], "mode": "headless"})
    check("安全门禁-拦截无代码", r.status_code == 422, f"detail={r.json().get('detail','')[:60]}")

# 正常执行
r = api("POST", f"/projects/{pid}/executions", json={
    "case_ids": [case_id_1, case_id_2],
    "mode": "headless",
    "batch_name": "联调执行批次",
})
check("创建执行", r.status_code == 200, f"data={r.json().get('data')}")
execution_id = r.json()["data"]["execution_id"]

# 轮询状态
print("  轮询执行进度...")
for i in range(30):
    time.sleep(2)
    r = api("GET", f"/executions/{execution_id}/status")
    s = r.json()["data"]
    status = s["status"]
    print(f"    [{i*2}s] status={status} progress={s.get('progress','?')} pct={s.get('percentage','?')}%")
    if status in ("completed", "failed", "stopped", "healing"):
        break

# 如果 heailing，等待完成
if status == "healing":
    for i in range(15):
        time.sleep(2)
        r = api("GET", f"/executions/{execution_id}/status")
        s = r.json()["data"]
        status = s["status"]
        if status in ("completed", "failed", "stopped"):
            break

check("执行完成", status == "completed", f"status={status}")

# 执行详情
r = api("GET", f"/executions/{execution_id}")
detail = r.json()["data"]
check("执行详情", r.status_code == 200, f"steps={len(detail.get('steps',[]))}")

# 步骤截图
steps = detail.get("steps", [])
has_screenshots = any(s.get("screenshot_before") or s.get("screenshot_after") for s in steps)
check("步骤截图", has_screenshots, f"steps with screenshots")

# 项目执行列表
r = api("GET", f"/projects/{pid}/executions")
check("项目执行列表", r.status_code == 200, f"items={len(r.json()['data']['items'])}")

# ═══════════════════════════════════════════════
# 6. 报告
# ═══════════════════════════════════════════════
print("\n--- 6. 报告 ---")

r = api("POST", f"/executions/{execution_id}/reports/generate")
check("生成报告", r.status_code == 200, f"data={r.json().get('data')}")

r = api("GET", f"/executions/{execution_id}/reports")
check("报告信息", r.status_code == 200, f"report_id={r.json()['data'].get('report_id')}")

# 报告HTML（静态文件，不走 API 前缀）
import httpx
download_url = r.json()["data"]["download_url"]
r = httpx.get(f"http://127.0.0.1:8000{download_url}", timeout=60)
check("报告HTML可访问", r.status_code == 200, f"size={len(r.text)} bytes")

# 内联检查
html = r.text
check("内联样式", "<style>" in html, "offline-ready")
check("Canvas图表", "donutChart" in html, "chart present")
check("Lightbox", "lightbox" in html, "screenshot viewer")
check("JSON导出", "exportJSON" in html, "export function")
check("CSV导出", "exportCSV" in html, "export function")
check("打印样式", "@media print" in html, "print styles")

# 重新生成（缓存）
r = api("POST", f"/executions/{execution_id}/reports/generate")
check("报告缓存", r.status_code == 200, f"cached")

# ═══════════════════════════════════════════════
# 7. 自愈 API
# ═══════════════════════════════════════════════
print("\n--- 7. 自愈 API ---")

r = api("GET", f"/executions/{execution_id}/heal-records")
check("自愈记录列表", r.status_code == 200, f"total={r.json()['data'].get('total',0)}")

# ═══════════════════════════════════════════════
# 8. 停止执行
# ═══════════════════════════════════════════════
print("\n--- 8. 停止执行（已完成的批次）---")

r = api("POST", f"/executions/{execution_id}/stop")
check("停止已完成的执行", r.status_code == 200, f"msg={r.json().get('message','')[:40]}")

# ═══════════════════════════════════════════════
# 清理
# ═══════════════════════════════════════════════
print("\n--- 清理 ---")
r = api("DELETE", f"/projects/{pid}")
check("删除项目", r.status_code == 200, str(r.status_code))

# ═══════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"结果: {passed} PASS / {failed} FAIL")
print(f"{'='*60}")
if failed > 0:
    sys.exit(1)