"""黄金路径 — 真实 Chromium + 确定性 Mock LLM 全链路（阶段 6）

链路: 元素感知(crawl) → Excel 导入 → AI 生成(Mock LLM) → Playwright 执行(真实 Chromium)
      → 截图 → 报告

设计约束（CI = Mock LLM + Real Chromium）:
  - CI 不调用真实 LLM（无网络波动 / 模型变化 / Token 成本 / 输出不稳定）
  - 确定性 Mock LLM 输出针对本地 Demo 页面的真实可执行代码
  - 真实 LLM + 真实 Chromium 仅用于人工验收 / Nightly：
    设置环境变量 AUTOPILOT_GOLDEN_REAL_LLM=1 + AUTOPILOT_GOLDEN_API_KEY=sk-xxx
    （可选 AUTOPILOT_GOLDEN_MODEL 指定模型）后运行本文件即为真实模式
  - 本地 Demo Web 站点（tests/demo_site）保证页面稳定可控、新环境可重复执行

前置条件:
  - 已安装 playwright + chromium（scripts/setup.sh / scripts/setup.ps1 完成）
  - 以 backend 为工作目录运行: .venv/bin/python -m pytest tests/integration/test_golden_path_real_chromium.py
  - 真实 LLM 模式示例:
      $env:AUTOPILOT_GOLDEN_REAL_LLM=1; $env:AUTOPILOT_GOLDEN_API_KEY="sk-xxx"
      .venv/bin/python -m pytest tests/integration/test_golden_path_real_chromium.py -s
"""

import io
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# IDE 沙箱会拦截 Playwright 子进程；真实浏览器执行必须显式关闭（app.main 亦已设置）
os.environ["TOOLHOST_SANDBOX_DISABLED"] = "true"

import pytest

from openpyxl import Workbook

DEMO_SITE_DIR = Path(__file__).resolve().parent.parent / "demo_site"


def _chromium_available() -> bool:
    """浏览器缺失时跳过（而非失败），并提示先执行 setup 脚本"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(),
    reason="Playwright Chromium 未安装，请先执行 scripts/setup.sh / scripts/setup.ps1",
)


# ── 确定性 Mock LLM 输出：针对 demo_site/index.html 的真实可执行代码 ──
GOLDEN_WEB_CODE = """\
async def run_test(safe):
    await safe.goto("{url}")
    await safe.wait(300)
    await safe.fill("#username", "admin")
    await safe.fill("#password", "secret123")
    await safe.select("#role", "admin")
    await safe.click("#login-btn")
    await safe.assert_text("#message", "欢迎, admin（admin）")
    return {{"success": True, "message": "golden-path-ok", "steps": []}}
"""


def _fake_call_openai(
    prompt: str,
    model: str,
    retries: int = 3,
    target_url: str = "",
    steps_json: str = "",
    platform: str = "web",
) -> str:
    """确定性 Mock LLM：替代真实模型调用，返回能真实跑通 Demo 页面的代码"""
    if platform == "android":
        return "def run_test(driver):\n    return {'success': True, 'steps': []}\n"
    return GOLDEN_WEB_CODE.format(url=target_url or "http://127.0.0.1:8000/")


# ═══════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════

@pytest.fixture(scope="module")
def demo_server():
    """启动本地 Demo Web 站点（tests/demo_site），随机端口，测试结束自动关闭"""

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DEMO_SITE_DIR), **kwargs)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"base_url": f"http://127.0.0.1:{port}", "port": port}
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def golden_env(monkeypatch, mock_settings, demo_server):
    """真实全链路环境：文件型 SQLite（后台执行线程可共享）+ SSRF 放行 + TestClient

    不使用 conftest 的 client/db_session（内存 StaticPool 单连接不支持
    orchestrator 后台线程的独立 SessionLocal 访问同一数据库）。
    """
    import tempfile

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    import app.db.database as dbmod
    from app.db.database import Base
    import app.main as main_module
    from app.dependencies import get_db

    app = main_module.app

    fd, db_path = tempfile.mkstemp(suffix="_golden.db")
    os.close(fd)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.commit()
    TestSession = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False,
    )

    # 全局 engine/SessionLocal 替换为文件型 SQLite：后台线程函数内
    # `from app.db.database import SessionLocal` 动态读取被 patch 的模块属性
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSession)
    Base.metadata.create_all(bind=engine)

    # lifespan 的 db_init 由测试自行建表；recover/cleanup 走空表无副作用
    monkeypatch.setattr(main_module, "db_init", lambda: None)

    # SSRF allowlist：放行 Demo 站点随机端口（入口校验 + 执行期策略共用）
    mock_settings("SSRF_ALLOWED_PORTS", str(demo_server["port"]))
    mock_settings("SSRF_ALLOWED_HOSTS", "127.0.0.1")

    # 确定性 Mock LLM（阶段 6：CI = Mock LLM + Real Chromium）
    # 真实 LLM 模式（人工验收 / Nightly）：设置环境变量
    #   AUTOPILOT_GOLDEN_REAL_LLM=1
    #   AUTOPILOT_GOLDEN_API_KEY=sk-xxx
    # 后跳过 Mock 注入，走真实模型 + 真实 Chromium。
    # 注意：conftest 会把 OPENAI_API_KEY 清空强制 Mock，此处显式恢复 settings，
    #       `_call_openai` 内部以 settings.OPENAI_API_KEY 为空作为 Mock 判定。
    if not os.environ.get("AUTOPILOT_GOLDEN_REAL_LLM"):
        monkeypatch.setattr("app.services.ai_service._call_openai", _fake_call_openai)
    else:
        real_key = os.environ.get("AUTOPILOT_GOLDEN_API_KEY", "").strip()
        assert real_key, "AUTOPILOT_GOLDEN_REAL_LLM=1 时必须同时设置 AUTOPILOT_GOLDEN_API_KEY"
        mock_settings("OPENAI_API_KEY", real_key)
        mock_settings("OPENAI_MODEL", os.environ.get("AUTOPILOT_GOLDEN_MODEL", "qwen3.5-flash"))

    def _override_db():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = _override_db

    try:
        with TestClient(app) as client:
            yield {
                "client": client,
                "session_factory": TestSession,
                "server": demo_server,
            }
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _wait_terminal_status(client, execution_id: int, timeout: float = 180) -> dict:
    """轮询执行状态直到进入终态；超时输出执行详情便于诊断"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/v1/executions/{execution_id}/status")
        assert resp.status_code == 200, resp.text
        last = resp.json()["data"]
        if last["status"] in ("completed", "failed", "stopped", "interrupted"):
            return last
        time.sleep(0.5)
    try:
        detail = client.get(f"/api/v1/executions/{execution_id}").json()["data"]
        steps_brief = [
            f"case{s['case_id']}#{s['step_index']}:{s['status']}:{s.get('error_message', '')[:100]}"
            for s in detail.get("steps", [])
        ]
    except Exception:
        steps_brief = []
    raise AssertionError(
        f"执行 {execution_id} 超时未进入终态, last={last}, steps={steps_brief[:20]}"
    )


def _wait_for_report(client, execution_id: int, timeout: float = 45) -> dict:
    """等待编排器自动生成报告（真实生产路径）；超时兜底手动触发"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/v1/executions/{execution_id}/reports")
        if resp.status_code == 200:
            return resp.json()["data"]
        time.sleep(1)
    # 兜底：执行已结束，手动生成（此时无并发，幂等返回）
    resp = client.post(f"/api/v1/executions/{execution_id}/reports/generate")
    assert resp.status_code == 200, f"手动生成报告失败: {resp.text}"
    resp = client.get(f"/api/v1/executions/{execution_id}/reports")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ═══════════════════════════════════════════════
# 黄金路径
# ═══════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.slow
def test_golden_path_real_chromium(golden_env):
    """crawl → import → generate → execute(真实 Chromium) → 截图 → 报告"""
    client = golden_env["client"]
    demo_url = golden_env["server"]["base_url"]

    # ── Step 1: 创建 Project（Web 平台） ──
    resp = client.post("/api/v1/projects/", json={
        "name": "Golden Path Demo",
        "target_url": demo_url,
        "test_path": "/",
    })
    assert resp.status_code == 200, resp.text
    pid = resp.json()["data"]["id"]

    # ── Step 2: 元素感知 — 真实 Chromium 抓取本地 Demo 页面 ──
    resp = client.post(f"/api/v1/projects/{pid}/elements/crawl", json={"max_depth": 1})
    assert resp.status_code == 200, resp.text
    crawl = resp.json()["data"]
    assert crawl["crawled_count"] >= 4, (
        f"期望至少 4 个可交互元素，实际 {crawl['crawled_count']}: {crawl.get('elements', [])[:10]}"
    )
    selectors = [e["selector"] for e in crawl["elements"]]
    for expected in ("#login-btn", "#username", "#password", "#role"):
        assert expected in selectors, f"抓取结果缺少 {expected}: {selectors}"

    # ── Step 3: Excel 用例导入 ──
    steps = [
        {"step_number": 1, "action": "navigate", "target": demo_url, "value": "", "description": "打开登录页"},
        {"step_number": 2, "action": "fill", "target": "#username", "value": "admin", "description": "输入用户名"},
        {"step_number": 3, "action": "fill", "target": "#password", "value": "secret123", "description": "输入密码"},
        {"step_number": 4, "action": "select", "target": "#role", "value": "admin", "description": "选择角色"},
        {"step_number": 5, "action": "click", "target": "#login-btn", "value": "", "description": "点击登录"},
        {"step_number": 6, "action": "assert_text", "target": "#message", "value": "欢迎, admin（admin）", "description": "断言欢迎语"},
    ]
    wb = Workbook()
    ws = wb.active
    ws.append(["用例名称", "操作步骤", "优先级"])
    ws.append(["Demo 登录", json.dumps(steps, ensure_ascii=False), "P0"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = client.post(
        f"/api/v1/projects/{pid}/cases/import",
        files={"file": ("golden.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    imp = resp.json()["data"]
    assert imp["total"] == 1 and imp["success"] == 1, f"Excel 导入失败: {imp}"

    resp = client.get(f"/api/v1/projects/{pid}/cases/", params={"page": 1, "size": 20})
    cases = resp.json()["data"]["items"]
    assert len(cases) == 1, cases
    case_id = cases[0]["id"]

    # ── Step 4: AI 生成（确定性 Mock LLM → 真实可执行 Safe API 代码） ──
    resp = client.post(f"/api/v1/projects/{pid}/cases/{case_id}/generate")
    assert resp.status_code == 200, resp.text
    gen = resp.json()["data"]
    assert gen["is_valid"] is True, f"生成代码未通过校验: {gen}"
    assert "safe.goto" in gen["code_content"], "生成的代码应使用 SafePlaywright API"
    assert demo_url in gen["code_content"], (
        f"生成的代码应包含 Demo 站点 URL {demo_url}，实际: {gen['code_content'][:200]}"
    )

    # ── Step 5: 执行 — 真实 Chromium 真实浏览器 ──
    resp = client.post(f"/api/v1/projects/{pid}/executions", json={
        "case_ids": [case_id], "mode": "headless", "batch_name": "GoldenPath",
    })
    assert resp.status_code == 200, resp.text
    eid = resp.json()["data"]["execution_id"]

    final = _wait_terminal_status(client, eid, timeout=180)

    # 先取详情：若失败，输出每一步状态与错误信息便于诊断
    resp = client.get(f"/api/v1/executions/{eid}")
    detail = resp.json()["data"]
    results = detail.get("case_results", [])
    if final["status"] != "completed" or final["passed_cases"] != 1:
        for s in (results[0]["steps"] if results else []):
            print(f"\n[STEP] #{s['step_index']} {s['action']} {s['target_selector']} -> {s['status']} | {s.get('error_message', '')[:300]}", flush=True)

    assert final["status"] == "completed", f"执行未完成: {final}"
    assert final["passed_cases"] == 1, f"用例应通过: {final}"

    # 执行详情：用例与步骤全部 success
    assert detail["status"] == "completed", detail
    assert results and results[0]["status"] == "success", f"用例应成功: {results}"
    assert all(s["status"] == "success" for s in results[0]["steps"]), results[0]["steps"]

    # ── Step 6: 截图产物（真实浏览器真实截图） ──
    case_id_exec = results[0]["case_id"]
    shot_dir = Path("uploads") / "screenshots" / str(eid) / str(case_id_exec)
    shots = sorted(shot_dir.glob("step_*.jpg")) if shot_dir.exists() else []
    assert shots, f"未找到执行截图: {shot_dir}"

    # ── Step 7: 报告（编排器自动生成 / 真实 Jinja2 渲染 + 文件落盘） ──
    info = _wait_for_report(client, eid, timeout=45)
    assert info["summary"]["passed"] == 1, f"报告统计错误: {info}"
    assert info["summary"]["failed"] == 0, f"报告统计错误: {info}"
    assert "report_id" in info and info["report_id"] > 0, info

    # 报告 HTML 文件真实存在
    from app.config import settings
    report_file = Path(settings.REPORT_DIR) / f"execution_{eid}_report.html"
    assert report_file.exists(), f"报告文件不存在: {report_file}"
