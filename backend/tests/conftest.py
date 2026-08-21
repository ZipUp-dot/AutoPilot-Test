# ----------------------------------------------------------------
# conftest.py -- 必须在任何 app 模块导入之前执行
# ----------------------------------------------------------------

import os
import sys
import uuid
import pathlib
import shutil
import tempfile

# 关键：必须在导入任何 app 模块之前强制设置测试环境变量
# 原因：pydantic-settings 单例在首次导入 app.config 时固化
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""  # 强制 Mock 模式
os.environ["_AUTOPILOT_TEST_MODE"] = "1"  # 安全标志

# 使用 uuid 生成唯一目录名，避免并发冲突和长期积累
# 使用 tempfile 获取平台兼容的临时目录
_test_run_id = str(uuid.uuid4())[:8]
_test_base_dir = os.path.join(tempfile.gettempdir(), f"autopilot_test_{_test_run_id}")

# 环境变量名必须使用单数形式，与 pydantic-settings 字段名匹配
env_var_map = {
    "uploads": "UPLOAD_DIR",
    "reports": "REPORT_DIR",
    "screenshots": "SCREENSHOT_DIR",
    "videos": "VIDEO_DIR",
    "excels": "EXCEL_DIR",
}
for subdir, env_var in env_var_map.items():
    path = pathlib.Path(os.path.join(_test_base_dir, subdir))
    path.mkdir(parents=True, exist_ok=True)
    os.environ[env_var] = str(path)

# 创建静态文件测试内容
(pathlib.Path(os.path.join(_test_base_dir, "uploads")) / "test.txt").write_text("hello")
(pathlib.Path(os.path.join(_test_base_dir, "reports")) / "test.html").write_text("<html>report</html>")

import pytest
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# -- 显式创建 SQLite 内存 engine，绝不复用 app.db.database 中的全局 engine --
TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=TEST_ENGINE,
    expire_on_commit=False,  # 防止对象过期
)

# 预导入 app.main：必须在 setup_test_database 之前完成
# 原因：StaticFiles 在模块导入时固化 directory 路径，后续 mock_settings 修改无效
import app.main  # noqa: E402


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """测试会话开始时创建所有表，使用 SQLAlchemy ORM 而非 schema.sql"""
    from app.db.database import Base
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_base_dir():
    """测试会话结束后清理本次运行创建的临时目录"""
    yield
    root = pathlib.Path(_test_base_dir)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="function")
def db_session():
    """每个测试函数独立事务，结束后回滚，确保数据隔离"""
    connection = TEST_ENGINE.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def mock_settings(request):
    """
    临时修改 settings 属性，兼容 Pydantic BaseSettings 的 frozen/validate 配置。
    使用 request.addfinalizer 注册恢复逻辑，不依赖 pytest 内部 API。
    """
    from app.config import settings

    def _setattr(name: str, value):
        original = getattr(settings, name, None)
        try:
            setattr(settings, name, value)
            request.addfinalizer(lambda n=name, o=original: setattr(settings, n, o))
        except (TypeError, Exception):
            object.__setattr__(settings, name, value)
            request.addfinalizer(lambda n=name, o=original: object.__setattr__(settings, n, o))

    return _setattr


@pytest.fixture
def client(db_session, request, suppress_lifespan_side_effects):
    """
    FastAPI TestClient，override get_db。
    使用 addfinalizer 确保 dependency_overrides 在异常时也能恢复。
    """
    from app.main import app
    from app.dependencies import get_db

    original_overrides = dict(app.dependency_overrides)

    def _restore_overrides():
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    request.addfinalizer(_restore_overrides)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_global_state():
    """自动清理模块级全局状态字典，防止测试间污染"""
    from app.routers import generate as gen_module
    from app.services import execution_state as es_module
    gen_module._batch_jobs.clear()
    es_module._stop_flags.clear()
    yield
    gen_module._batch_jobs.clear()
    es_module._stop_flags.clear()


@pytest.fixture(autouse=False)
def block_background_threads(mocker):
    """
    阻止被测代码启动真实后台线程，防止测试间交叉污染。
    同时 patch 两种可能的 threading 导入方式：
    - import threading → threading.Thread.start
    - from threading import Thread → Thread.start
    """
    modules = [
        "app.routers.generate",
        "app.services.orchestrator",
        "app.services.playwright_service",
    ]
    for mod in modules:
        try:
            mocker.patch(f"{mod}.threading.Thread.start")   # import threading
        except AttributeError:
            pass  # module does not import threading
        try:
            mocker.patch(f"{mod}.Thread.start")             # from threading import Thread
        except AttributeError:
            pass  # module does not use `from threading import Thread`
    yield


@pytest.fixture
def mock_threading_in_routers(mocker):
    """供测试显式断言路由层 Thread 调用参数"""
    return mocker.patch("app.routers.generate.threading.Thread")


@pytest.fixture
def mock_threading_in_orchestrator(mocker):
    """供测试显式断言编排器 Thread 调用参数

    注意：编排器在函数内 import threading，不是模块级导入，
    所以必须 patch 全局 threading.Thread 而非 app.services.orchestrator.threading.Thread
    """
    import threading
    return mocker.patch.object(threading, "Thread")


@pytest.fixture
def mock_threading_in_playwright(mocker):
    """供测试显式断言 Playwright 服务 Thread 调用参数"""
    return mocker.patch("app.services.playwright_service.threading.Thread")


@pytest.fixture
def mock_monitor_task(mocker):
    """阻止编排器创建真实的后台监听协程，避免测试事件循环挂起。

    run_execute_only / run_full_pipeline 会 asyncio.create_task 启动
    _monitor_and_generate_report 轮询协程（每 2s 轮询，最多 30 分钟）。
    若不拦截，测试会一直等待该协程结束而挂起。
    """
    async def _noop(self, execution_id):
        return None

    return mocker.patch(
        "app.services.orchestrator.TestOrchestrator._monitor_and_generate_report",
        _noop,
    )


@pytest.fixture
def mock_llm(mocker):
    """Mock _call_openai，返回包含 markdown 代码块的字符串"""
    return mocker.patch(
        "app.services.ai_service._call_openai",
        return_value=(
            "```python\n"
            "async def run_test(page):\n"
            '    return {"success": True, "steps": []}\n'
            "```"
        ),
    )


@pytest.fixture
def mock_prompt_file(tmp_path, monkeypatch):
    """为 Prompt 热更新测试提供临时文件，通过 monkeypatch 修改 __file__ 路径"""
    prompts_dir = tmp_path / "app" / "prompts"
    prompts_dir.mkdir(parents=True)
    template_file = prompts_dir / "generate_prompt.txt"
    template_file.write_text(
        "Case: {case_name}\nSteps: {steps_json}\nElements: {elements_list}\nURL: {target_url}",
        encoding="utf-8",
    )
    import app.services.ai_service as ai_module
    monkeypatch.setattr(
        ai_module, "__file__",
        str(tmp_path / "app" / "services" / "ai_service.py")
    )
    return template_file


@pytest.fixture
def mock_playwright_for_element_service(mocker):
    """为 element_service.py 提供 Playwright mock（patch playwright 库中的导入）"""
    mock_page = mocker.AsyncMock()
    mock_page.evaluate.return_value = []
    mock_page.goto.return_value = None
    mock_page.screenshot.return_value = b"fake_image"
    mock_page.query_selector_all.return_value = []

    mock_context = mocker.AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = mocker.AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw = mocker.AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mocker.patch(
        "playwright.async_api.async_playwright",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_pw),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )
    return mock_page


@pytest.fixture
def mock_playwright_for_execution_service(mocker):
    """为 playwright_service.py 提供 Playwright mock"""
    mock_page = mocker.AsyncMock()
    mock_page.goto.return_value = None
    mock_page.screenshot.return_value = b"fake_image"
    # set_default_timeout 是 Playwright 同步方法，必须用 MagicMock 而非 AsyncMock，
    # 否则生产代码同步调用时会创建未 await 的协程（RuntimeWarning）
    mock_page.set_default_timeout = mocker.MagicMock(return_value=None)

    mock_context = mocker.AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = mocker.AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw = mocker.AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mocker.patch(
        "playwright.async_api.async_playwright",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_pw),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )
    return mock_page


@pytest.fixture
def suppress_lifespan_side_effects(mocker):
    """
    消除 lifespan startup 中的真实文件系统/数据库副作用。
    """
    mocker.patch("app.main.db_init")
    mocker.patch("app.services.report_service.ReportService.cleanup_old_reports")


@pytest.fixture
def factories(db_session):
    """注入数据库 session 到所有工厂"""
    from tests import factories as f
    for factory_cls in [
        f.ProjectFactory, f.TestCaseFactory, f.PageElementFactory,
        f.GeneratedCodeFactory, f.ExecutionFactory,
        f.ExecutionStepFactory, f.ExecutionReportFactory, f.HealRecordFactory,
    ]:
        factory_cls._meta.sqlalchemy_session = db_session
    return f


# ═══════════════════════════════════════════════
# Phase 2: 新增 fixtures
# ═══════════════════════════════════════════════

# ── Mock LLM 变体 ──

@pytest.fixture
def mock_llm_invalid_code(mocker):
    """Mock _call_openai 返回语法错误的代码"""
    return mocker.patch(
        "app.services.ai_service._call_openai",
        return_value="def broken( {{{",
    )


@pytest.fixture
def mock_llm_network_error(mocker):
    """Mock _call_openai 模拟网络错误"""
    import httpx
    return mocker.patch(
        "app.services.ai_service._call_openai",
        side_effect=httpx.ConnectError("Connection refused"),
    )


@pytest.fixture
def mock_llm_retry_then_success(mocker):
    """Mock _call_openai 前2次失败，第3次成功"""
    import httpx
    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise httpx.TimeoutException("Timeout")
        return "async def run_test(page):\n    return {'success': True}\n"

    return mocker.patch("app.services.ai_service._call_openai", side_effect=side_effect)


# ── Mock httpx ──

@pytest.fixture
def mock_httpx(mocker):
    """同时 patch ai_service 和 heal_service 中的 httpx.Client"""
    mock_response = mocker.MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "async def run_test(page):\n    return {'success': True}\n"}}],
        "usage": {"total_tokens": 42},
    }
    mock_client = mocker.MagicMock()
    mock_client.__enter__.return_value.post.return_value = mock_response
    mocker.patch("app.services.ai_service.httpx.Client", return_value=mock_client)
    mocker.patch("app.services.heal_service.httpx.Client", return_value=mock_client)
    return mock_client


# ── Mock 文件系统 ──

@pytest.fixture
def mock_file_ops(mocker):
    """Patch Path.mkdir/Path.write_text，隔离文件写入副作用。

    注意：
    1. 不 patch builtins.open —— openpyxl 在 wb.save()/load_workbook() 内部
       以二进制模式打开临时文件，mock_open 返回 str 会导致
       zipfile.write 报 "memoryview: bytes-like object required"。
    2. 不 patch os.makedirs —— case_service._save_upload 依赖它真实创建
       隔离临时目录后再 open("wb")，否则会 FileNotFoundError。
       上传目录已由 conftest 用 uuid 隔离，写入是安全的。
    """
    mocker.patch("pathlib.Path.mkdir")
    mocker.patch("pathlib.Path.write_text")
    mocker.patch("shutil.rmtree")


# ── Mock Playwright for heal router ──

@pytest.fixture
def mock_playwright_for_heal_router(mocker):
    """为 heal router 提供独立的 Playwright mock"""
    mock_page = mocker.AsyncMock()
    mock_page.goto.return_value = None
    mock_page.content.return_value = "<html></html>"
    mock_page.evaluate.return_value = []
    mock_page.screenshot.return_value = b"fake_image"
    # set_default_timeout 是 Playwright 同步方法，必须用 MagicMock（见 heal.py 中同步调用）
    mock_page.set_default_timeout = mocker.MagicMock(return_value=None)

    mock_context = mocker.AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = mocker.AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw = mocker.AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mocker.patch(
        "playwright.async_api.async_playwright",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_pw),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )
    return mock_page


# ── Mock Jinja2 ──

@pytest.fixture
def mock_jinja_template(mocker):
    """Mock report_service 的 Jinja2 模板渲染"""
    mock_template = mocker.MagicMock()
    mock_template.render.return_value = "<html><body>Test Report</body></html>"
    mocker.patch("app.services.report_service._env.get_template", return_value=mock_template)
    return mock_template


# ── 便捷数据 fixtures ──

@pytest.fixture
def sample_project(db_session):
    """创建一个测试项目"""
    from app.models.project import Project
    project = Project(
        name="Test Project",
        target_url="https://example.com",
        test_path="/",
        browser_type="chromium",
        headless=1,
        status="active",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def sample_test_case(db_session, sample_project):
    """创建一个带步骤的测试用例"""
    import json
    from app.models.test_case import TestCase
    case = TestCase(
        project_id=sample_project.id,
        case_name="Login Test",
        case_no="TC001",
        priority="P0",
        steps=json.dumps([
            {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "Open page"},
            {"step_number": 2, "action": "fill", "target": "#username", "value": "admin", "description": "Enter username"},
        ]),
        status="imported",
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


@pytest.fixture
def sample_generated_code(db_session, sample_test_case):
    """创建一个已生成代码记录"""
    from app.models.generated_code import GeneratedCode
    code = GeneratedCode(
        case_id=sample_test_case.id,
        code_content="async def run_test(page):\n    return {'success': True, 'steps': []}",
        code_language="python",
        is_valid=1,
    )
    db_session.add(code)
    db_session.commit()
    db_session.refresh(code)
    return code


@pytest.fixture
def sample_execution(db_session, sample_project, sample_test_case):
    """创建一个已完成执行记录"""
    from datetime import datetime as dt
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep
    exec_obj = Execution(
        project_id=sample_project.id,
        batch_name="Test Batch",
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        status="completed",
        start_time=dt.utcnow(),
        end_time=dt.utcnow(),
    )
    db_session.add(exec_obj)
    db_session.flush()
    step = ExecutionStep(
        execution_id=exec_obj.id,
        case_id=sample_test_case.id,
        step_index=1,
        action="navigate",
        status="success",
        duration_ms=100,
    )
    db_session.add(step)
    db_session.commit()
    db_session.refresh(exec_obj)
    return exec_obj


@pytest.fixture
def sample_page_element(db_session, sample_project):
    """创建一个页面元素"""
    from app.models.element import PageElement
    el = PageElement(
        project_id=sample_project.id,
        element_type="button",
        tag_name="button",
        selector="#submit-btn",
        text_content="Submit",
        is_visible=1,
    )
    db_session.add(el)
    db_session.commit()
    db_session.refresh(el)
    return el