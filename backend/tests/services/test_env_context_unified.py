"""P1-1 环境上下文全链路统一 — 验收测试

6 条验收：
  1. test_path=/login 时 5 处 URL 完全一致：crawl / AI Prompt / 执行前健康检查
     （Admission 前，Project 当前值）与 execution goto / heal 导航（Admission 后，
     Manifest 冻结快照）；改 project.target_url 不影响已 Admission 的 Execution
  2. browser_type=firefox 时【三处】launch 均以 firefox 启动
     （主执行 _execute_async / 自动 Heal _start_healing / 手动 Heal trigger_heal）
  3. execution_mode=headed 时三处 launch 均 headed（headless=False）
  4. SSRF：169.254.169.254 被入口校验与执行期策略同时拦截
  5. 全库搜索证明无写死 chromium 的 launch 残留
  6. Admission 后 UrlPolicy 只读 Manifest.ssrf_policy，改 project.config_json 的
     allowed_hosts 不影响已 Admission 的 Execution 的 SSRF 判定
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.url_builder import build_target_url


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _manifest(project, browser_type="chromium", execution_mode="headless",
              allowed_hosts=None):
    """构造 Manifest 冻结快照（与 admission.materialize 写入的结构一致）。"""
    test_path = getattr(project, "test_path", "/") or "/"
    return json.dumps({
        "target_url": project.target_url,
        "test_path": test_path,
        "browser_type": browser_type,
        "execution_mode": execution_mode,
        "ssrf_policy": {
            "allowed_hosts": list(allowed_hosts or []),
            "allowed_ports": [],
        },
        "project": {
            "name": project.name,
            "target_url": project.target_url,
            "test_path": test_path,
        },
    })


def _patch_async_playwright(mocker, browser_type, launch_calls, goto_urls):
    """构造 async_playwright mock：launch 记录 browser_type 调用，page.goto 记录 URL。"""
    mock_page = mocker.AsyncMock()
    mock_page.set_default_timeout = mocker.MagicMock(return_value=None)
    mock_page.goto = mocker.AsyncMock(side_effect=lambda *a, **k: goto_urls.append(a[0]))
    mock_context = mocker.AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = mocker.AsyncMock()
    mock_browser.new_context.return_value = mock_context

    launcher = mocker.AsyncMock()
    launcher.launch = mocker.AsyncMock(
        side_effect=lambda **k: launch_calls.append((browser_type, k)) or mock_browser
    )
    mock_pw = mocker.AsyncMock()
    setattr(mock_pw, browser_type, launcher)
    mocker.patch(
        "playwright.async_api.async_playwright",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_pw),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )
    return mock_page


class TestBuildTargetUrl:
    """build_target_url 单一入口：只收两个字符串，规范化拼接"""

    def test_join_normal(self):
        assert build_target_url("https://app.example.com", "/login") == \
            "https://app.example.com/login"

    def test_strips_extra_slashes(self):
        assert build_target_url("https://app.example.com/", "/login") == \
            "https://app.example.com/login"

    def test_trailing_slash_kept(self):
        assert build_target_url("https://app.example.com", "/login/") == \
            "https://app.example.com/login/"

    def test_empty_yields_root(self):
        assert build_target_url("", "") == "/"


@pytest.fixture
def login_project(db_session):
    """test_path=/login 的 Web 项目（browser_type=firefox，config_json 带 allowlist）"""
    from app.models.project import Project
    p = Project(
        name="Login Proj",
        target_url="https://app.example.com",
        test_path="/login",
        browser_type="firefox",
        headless=1,
        status="active",
        config_json=json.dumps({"allowed_hosts": ["internal.example"]}),
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def login_case(db_session, login_project):
    from app.models.test_case import TestCase
    case = TestCase(
        project_id=login_project.id,
        case_name="Login",
        case_no="P11-01",
        priority="P0",
        steps=json.dumps([
            {"step_number": 1, "action": "navigate",
             "target": "https://app.example.com/login", "value": "",
             "description": "Open login"},
        ]),
        status="imported",
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


class TestFiveUrlsConsistent:
    """验收 1：test_path=/login 时 5 处 URL 完全一致"""

    def test_crawl_uses_project_context(self, db_session, login_project, mocker):
        """crawl：Admission 前上下文 → build_target_url(project.target_url, test_path)"""
        captured = {}

        async def fake_extract(self, url, browser_type, timeout_ms=None, policy=None):
            captured["url"] = url
            return []

        from app.services.element_service import ElementService
        mocker.patch.object(ElementService, "_extract_elements", new=fake_extract)
        svc = ElementService(db_session)
        import asyncio
        asyncio.run(svc.crawl(login_project.id))

        assert captured["url"] == "https://app.example.com/login"

    def test_prompt_uses_project_context(self, db_session, login_project, login_case, mocker):
        """AI Prompt：Admission 前上下文 → target_url 传入 _call_openai"""
        captured = {}
        from app.services.ai_service import AIService

        def fake_call(prompt, model, **kwargs):
            captured["target_url"] = kwargs.get("target_url")
            return "```python\ndef run_test(safe):\n    return True\n```"

        mocker.patch("app.services.ai_service._call_openai", side_effect=fake_call)
        mocker.patch("app.services.ai_service.CodeValidator.validate", return_value=None)

        AIService(db_session).generate_single(login_project.id, login_case.id)

        assert captured["target_url"] == "https://app.example.com/login"

    def test_pre_check_uses_project_context(self, db_session, mocker):
        """执行前健康检查：Admission 前上下文 → build_target_url(project 值)"""
        from app.models.project import Project
        proj = Project(
            name="PreCheck Proj",
            target_url="https://pre.example.com",
            test_path="/login",
            browser_type="chromium",
            headless=1,
            status="active",
        )
        db_session.add(proj)
        db_session.commit()
        db_session.refresh(proj)

        captured = {}

        class _FakeResp:
            status_code = 200

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kwargs):
                captured["url"] = url
                return _FakeResp()

        mocker.patch("httpx.AsyncClient", return_value=_FakeClient())
        mocker.patch("app.db.database.SessionLocal", return_value=db_session)
        from app.services.orchestrator import TestOrchestrator
        import asyncio
        err = asyncio.run(TestOrchestrator()._pre_execution_check(proj.id, "web"))

        assert err is None
        assert captured["url"] == "https://pre.example.com/login"

    def test_execution_goto_uses_manifest_and_ignores_project_change(
        self, db_session, login_project, login_case, mocker
    ):
        """execution goto：Admission 后上下文 → Manifest 冻结值；改 project.target_url 不受影响"""
        from app.models.execution import Execution
        from app.services.playwright_service import PlaywrightService

        launch_calls, goto_urls = [], []
        _patch_async_playwright(mocker, "chromium", launch_calls, goto_urls)
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())
        mocker.patch.object(PlaywrightService, "_execute_case", return_value=True)
        mocker.patch.object(PlaywrightService, "_start_healing")

        exec_obj = Execution(
            project_id=login_project.id,
            total_cases=1,
            status="queued",
            start_time=__import__("datetime").datetime.utcnow(),
            manifest_json=_manifest(login_project),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        # Admission 后修改 Project 当前值（模拟上线后配置漂移）
        login_project.target_url = "https://evil.example.com"
        db_session.commit()

        svc = PlaywrightService(db_session)
        import asyncio
        asyncio.run(svc._execute_async(
            login_project.id, [login_case.id], exec_obj.id, "headless"))

        assert goto_urls == ["https://app.example.com/login"]
        assert exec_obj.status == "completed"

    def test_heal_navigation_uses_manifest(self, db_session, login_project, login_case, mocker):
        """自动 Heal 导航：Admission 后上下文 → Manifest 冻结值"""
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.services.playwright_service import PlaywrightService

        launch_calls, goto_urls = [], []
        _patch_async_playwright(mocker, "firefox", launch_calls, goto_urls)
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())
        mocker.patch("app.db.database.SessionLocal", return_value=db_session)
        heal_result = SimpleNamespace(
            heal_id=1, healed_code="x", retry_status="success",
            error_type=None, error_message=None,
        )
        mocker.patch(
            "app.services.heal_service.HealRoundService",
            **{"return_value.heal_case": mocker.AsyncMock(return_value=heal_result)},
        )
        _SyncThread.patch(mocker)

        exec_obj = Execution(
            project_id=login_project.id,
            total_cases=1,
            status="healing",
            start_time=__import__("datetime").datetime.utcnow(),
            manifest_json=_manifest(login_project, browser_type="firefox"),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)
        db_session.add(ExecutionStep(
            execution_id=exec_obj.id, case_id=login_case.id,
            step_index=1, status="failed",
        ))
        db_session.commit()

        svc = PlaywrightService(db_session)
        svc._start_healing(exec_obj.id, [login_case.id])

        assert goto_urls == ["https://app.example.com/login"]


class _SyncThread:
    """测试替身：Thread.start() 同步执行 target，等价单线程运行"""
    def __init__(self, target=None, daemon=False, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()

    @staticmethod
    def patch(mocker):
        mocker.patch("app.services.playwright_service.threading.Thread", _SyncThread)


class TestThreeLaunchesFromManifest:
    """验收 2+3：firefox + headed 时三处 launch 均 firefox / headed"""

    def test_main_execution_launch(self, db_session, login_project, login_case, mocker):
        """主执行 launch：firefox + headed"""
        from app.models.execution import Execution
        from app.services.playwright_service import PlaywrightService

        launch_calls, goto_urls = [], []
        _patch_async_playwright(mocker, "firefox", launch_calls, goto_urls)
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())
        mocker.patch.object(PlaywrightService, "_execute_case", return_value=True)
        mocker.patch.object(PlaywrightService, "_start_healing")

        exec_obj = Execution(
            project_id=login_project.id,
            total_cases=1,
            status="queued",
            start_time=__import__("datetime").datetime.utcnow(),
            manifest_json=_manifest(
                login_project, browser_type="firefox", execution_mode="headed"),
        )
        db_session.add(exec_obj)
        db_session.commit()

        svc = PlaywrightService(db_session)
        import asyncio
        asyncio.run(svc._execute_async(
            login_project.id, [login_case.id], exec_obj.id, "headless"))

        assert launch_calls == [("firefox", {"headless": False})]

    def test_auto_heal_launch(self, db_session, login_project, login_case, mocker):
        """自动 Heal launch：firefox + headed"""
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.services.playwright_service import PlaywrightService

        launch_calls, goto_urls = [], []
        _patch_async_playwright(mocker, "firefox", launch_calls, goto_urls)
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())
        mocker.patch("app.db.database.SessionLocal", return_value=db_session)
        heal_result = SimpleNamespace(
            heal_id=1, healed_code="x", retry_status="success",
            error_type=None, error_message=None,
        )
        mocker.patch(
            "app.services.heal_service.HealRoundService",
            **{"return_value.heal_case": mocker.AsyncMock(return_value=heal_result)},
        )
        _SyncThread.patch(mocker)

        exec_obj = Execution(
            project_id=login_project.id,
            total_cases=1,
            status="healing",
            start_time=__import__("datetime").datetime.utcnow(),
            manifest_json=_manifest(
                login_project, browser_type="firefox", execution_mode="headed"),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)
        db_session.add(ExecutionStep(
            execution_id=exec_obj.id, case_id=login_case.id,
            step_index=1, status="failed",
        ))
        db_session.commit()

        svc = PlaywrightService(db_session)
        svc._start_healing(exec_obj.id, [login_case.id])

        assert launch_calls == [("firefox", {"headless": False})]

    def test_manual_heal_launch(self, client, db_session, login_project, login_case, mocker):
        """手动 Heal（POST /executions/{id}/heal）launch：firefox + headed"""
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep

        launch_calls, goto_urls = [], []
        _patch_async_playwright(mocker, "firefox", launch_calls, goto_urls)
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())
        heal_result = SimpleNamespace(
            heal_id=1, healed_code="def run_test(safe):\n    pass",
            retry_status="success", error_type=None, error_message=None,
        )
        mocker.patch(
            "app.services.heal_service.HealRoundService",
            **{"return_value.heal_case": mocker.AsyncMock(return_value=heal_result)},
        )

        exec_obj = Execution(
            project_id=login_project.id,
            total_cases=1,
            status="healing",
            start_time=__import__("datetime").datetime.utcnow(),
            manifest_json=_manifest(
                login_project, browser_type="firefox", execution_mode="headed"),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)
        db_session.add(ExecutionStep(
            execution_id=exec_obj.id, case_id=login_case.id,
            step_index=1, status="failed",
        ))
        db_session.commit()

        resp = client.post(
            f"/api/v1/executions/{exec_obj.id}/heal",
            json={"case_id": login_case.id, "step_index": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        assert launch_calls == [("firefox", {"headless": False})]
        assert goto_urls == ["https://app.example.com/login"]


class TestSsrf:
    """验收 4：169.254.169.254 被拦（入口校验 + 执行期策略双层）"""

    def test_validate_blocks_metadata(self):
        from app.utils.url_policy import validate_target_url
        err = validate_target_url("http://169.254.169.254/latest/meta-data/")
        assert err is not None
        assert "169.254.169.254" in err

    def test_policy_blocks_metadata(self):
        from app.utils.url_policy import UrlPolicy
        policy = UrlPolicy("https://app.example.com/login")
        assert policy.is_allowed("http://169.254.169.254/latest/meta-data/") is False
        assert policy.is_allowed("http://169.254.169.254/") is False


class TestNoHardcodedChromiumLaunch:
    """验收 5：全库搜索无写死 chromium 的 launch 残留"""

    def test_no_chromium_launch_in_app(self):
        hits = []
        for path in (BACKEND_ROOT / "app").rglob("*.py"):
            if path.name == "url_policy.py":
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if "禁止" in line:  # 跳过禁令说明（如 _resolve_launcher docstring）
                    continue
                if re.search(r"chromium\s*\.\s*launch\s*\(", line):
                    hits.append(f"{path.relative_to(BACKEND_ROOT)}:{lineno}")
        assert hits == [], f"存在写死 chromium.launch 的残留: {hits}"


class TestSsrfSnapshotFrozen:
    """验收 6：Admission 后 UrlPolicy 只读 Manifest.ssrf_policy，config_json 变更不影响"""

    def test_manifest_frozen_after_config_change(self, db_session, login_project, login_case):
        from app.services.execution_admission_service import ExecutionAdmissionService
        from app.models.generated_code import GeneratedCode
        from app.utils.step_canonicalizer import hash_steps

        # Admission check 要求 case 有有效生成代码（code_id 冻结来源）
        db_session.add(GeneratedCode(
            case_id=login_case.id,
            code_content="async def run_test(page):\n    return {'success': True, 'steps': []}",
            code_language="python",
            is_valid=1,
            source_steps_hash=hash_steps(json.loads(login_case.steps)),
        ))
        db_session.commit()

        svc = ExecutionAdmissionService(db_session)
        result = svc.admit(login_project.id, [login_case.id], execution_mode="headed")
        assert result.ok
        exec_id = svc.materialize(result, batch_name="P1-1")

        db_session.refresh(login_project)
        # Admission 后修改 config_json 的 allowed_hosts（配置漂移）
        login_project.config_json = json.dumps({"allowed_hosts": ["drifted.example"]})
        db_session.commit()

        from app.models.execution import Execution as ExecModel
        from app.services.playwright_service import _load_manifest, _env_from_manifest
        env = _env_from_manifest(_load_manifest(db_session.query(ExecModel).get(exec_id)))

        # Manifest 冻结的是 Admission 时刻的 allowlist
        assert env["allowed_hosts"] == ["internal.example"]
        # 冻结值参与执行期策略判定：internal.example 放行、drifted.example 不放行
        from app.utils.url_policy import UrlPolicy
        policy = UrlPolicy(
            build_target_url(env["target_url"], env["test_path"]),
            allowed_hosts=env["allowed_hosts"],
            allowed_ports=env["allowed_ports"],
        )
        assert policy.is_allowed("http://internal.example/x") is True
        assert policy.is_allowed("http://drifted.example/x") is False
