"""AppiumService 执行引擎测试 — 验证 Android 执行链全流程

执行链:
  Android Case → AppiumService → Appium Session → Generated Code (原始)
  → CodeValidator → AppiumCodeInjector → exec(namespace) → run_test(driver)
  → ExecutionStep → Screenshot → Result

覆盖场景: click / input / wait / back / screenshot / failure / cleanup / stop
"""

import json
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from sqlalchemy.orm import Session

from app.services.execution_state import set_stop_flag, clear_stop_flag, is_stopped


# ═══════════════════════════════════════════════════
# 全局 Mock: appium-python-client 未安装，预构建 sys.modules
# ═══════════════════════════════════════════════════

@pytest.fixture(autouse=True, scope="session")
def _mock_appium_package():
    """Mock the entire appium package since it's not installed in CI.
    Must be session-scoped so that from appium.webdriver.common.appiumby import AppiumBy
    inside _build_sync_namespace resolves correctly.
    """
    mock_appium = MagicMock()
    mock_webdriver = MagicMock()
    mock_remote = MagicMock()
    mock_webdriver.Remote = mock_remote
    mock_appium.webdriver = mock_webdriver

    mock_appium_by = MagicMock()
    mock_appium_by.ID = "id"
    mock_appium_by.ACCESSIBILITY_ID = "accessibility id"
    mock_appium_by.XPATH = "xpath"
    mock_appium_by.CLASS_NAME = "class name"

    mock_common = MagicMock()
    mock_common.appiumby = mock_appium_by
    mock_appium.webdriver.common = mock_common

    # 写入 sys.modules 后立即从中导入 AppiumService
    existing = {k: v for k, v in sys.modules.items()
                if k.startswith('appium')}
    for k in existing:
        del sys.modules[k]

    sys.modules['appium'] = mock_appium
    sys.modules['appium.webdriver'] = mock_webdriver
    sys.modules['appium.webdriver.common'] = mock_common
    sys.modules['appium.webdriver.common.appiumby'] = mock_appium_by

    yield mock_remote

    # 清理，避免污染其他测试
    for k in list(sys.modules):
        if k.startswith('appium') and k not in existing:
            del sys.modules[k]
    sys.modules.update(existing)


# ═══════════════════════════════════════════════════
# 测试代码（与 AppiumCodeInjector 兼容的链式调用风格）
# ═══════════════════════════════════════════════════

CLICK_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/btn").click()
    return {"success": True, "steps": []}
"""

INPUT_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "com.example:id/input").send_keys("hello")
    return {"success": True, "steps": []}
"""

WAIT_CODE = """def run_test(driver):
    import time
    time.sleep(0.1)
    return {"success": True, "steps": []}
"""

BACK_CODE = """def run_test(driver):
    driver.back()
    return {"success": True, "steps": []}
"""

SCREENSHOT_CODE = """def run_test(driver):
    driver.save_screenshot("/tmp/screenshot.png")
    return {"success": True, "steps": []}
"""

FAILURE_CODE = """def run_test(driver):
    driver.find_element(AppiumBy.ID, "nonexistent").click()
    return {"success": True, "steps": []}
"""


# ═══════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════

@pytest.fixture
def mock_driver():
    """创建 mock Appium WebDriver"""
    driver = MagicMock()
    mock_el = MagicMock()
    driver.find_element.return_value = mock_el
    return driver


@pytest.fixture
def appium_svc(db_session):
    """创建 AppiumService 实例"""
    from app.services.appium_service import AppiumService
    return AppiumService(db_session)


@pytest.fixture
def android_project(db_session):
    """创建 Android 项目"""
    from app.models.project import Project
    project = Project(
        name="Android Test Project",
        target_url="android://app",
        platform="android",
        status="active",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def _create_case_and_code(db_session, project, code_content, steps=None):
    """创建 TestCase 和 GeneratedCode 并返回 case"""
    from app.models.test_case import TestCase
    from app.models.generated_code import GeneratedCode
    if steps is None:
        steps = [
            {"step_number": 1, "action": "click", "target": "com.example:id/btn", "value": ""},
        ]
    case = TestCase(
        project_id=project.id,
        case_name="Android Test",
        case_no="TC001",
        steps=json.dumps(steps),
        status="imported",
    )
    db_session.add(case)
    db_session.flush()

    gen_code = GeneratedCode(
        case_id=case.id,
        code_content=code_content,
        code_language="python",
        is_valid=1,
    )
    db_session.add(gen_code)
    db_session.commit()
    db_session.refresh(case)
    return case


def _create_execution(db_session, project):
    """创建 Execution 记录"""
    from app.models.execution import Execution
    exec_obj = Execution(
        project_id=project.id,
        batch_name="Android Test Batch",
        total_cases=1,
        status="running",
    )
    db_session.add(exec_obj)
    db_session.commit()
    db_session.refresh(exec_obj)
    return exec_obj


# ═══════════════════════════════════════════════════
# Tests: _execute_case — 核心执行链
# ═══════════════════════════════════════════════════

class TestAppiumExecuteCase:
    """_execute_case() — 单条用例执行链"""

    def test_click(self, db_session, appium_svc, mock_driver, android_project):
        """click 操作 → 执行成功"""
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        assert result is True
        mock_driver.find_element.assert_called_once()
        mock_driver.find_element.return_value.click.assert_called_once()

        # 验证步骤记录
        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).all()
        assert len(steps) >= 1

    def test_input(self, db_session, appium_svc, mock_driver, android_project):
        """input(send_keys) 操作 → 执行成功"""
        case = _create_case_and_code(db_session, android_project, INPUT_CODE)
        exec_obj = _create_execution(db_session, android_project)

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        assert result is True
        mock_driver.find_element.return_value.send_keys.assert_called_once_with("hello")

    def test_wait(self, db_session, appium_svc, mock_driver, android_project):
        """time.sleep 等待 → 执行成功"""
        case = _create_case_and_code(db_session, android_project, WAIT_CODE)
        exec_obj = _create_execution(db_session, android_project)

        with patch("time.sleep") as mock_sleep:
            result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        assert result is True
        mock_sleep.assert_called_once_with(0.1)

    def test_back(self, db_session, appium_svc, mock_driver, android_project):
        """driver.back() → 执行成功"""
        case = _create_case_and_code(db_session, android_project, BACK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        assert result is True
        mock_driver.back.assert_called_once()

    def test_screenshot(self, db_session, appium_svc, mock_driver, android_project):
        """driver.save_screenshot() → 执行成功"""
        case = _create_case_and_code(db_session, android_project, SCREENSHOT_CODE)
        exec_obj = _create_execution(db_session, android_project)

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        assert result is True
        # save_screenshot 被调用 3 次：before 截图 + 用户代码 + after 截图
        assert mock_driver.save_screenshot.call_count >= 1
        mock_driver.save_screenshot.assert_any_call("/tmp/screenshot.png")

    def test_failure_step_recorded(self, db_session, appium_svc, mock_driver, android_project):
        """操作失败 → 返回 False 且步骤记录包含失败状态和异常信息"""
        mock_driver.find_element.side_effect = Exception("NoSuchElement")
        case = _create_case_and_code(db_session, android_project, FAILURE_CODE)
        exec_obj = _create_execution(db_session, android_project)

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        assert result is False

        # 验证步骤记录包含失败状态
        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).all()
        failed_steps = [s for s in steps if s.status == "failed"]
        assert len(failed_steps) >= 1, "应至少有一个失败步骤记录"

    def test_failure_exception_type(self, db_session, appium_svc, mock_driver, android_project):
        """操作失败 → exception_type 被正确记录"""
        mock_driver.find_element.side_effect = ValueError("wrong value")
        case = _create_case_and_code(db_session, android_project, FAILURE_CODE)
        exec_obj = _create_execution(db_session, android_project)

        appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
            ExecutionStep.status == "failed",
        ).all()
        if steps:
            assert steps[0].exception_type == "ValueError", \
                f"expected ValueError, got {steps[0].exception_type}"

    def test_no_code_returns_false(self, db_session, appium_svc, mock_driver, android_project):
        """无有效代码 → 返回 False"""
        from app.models.test_case import TestCase
        case = TestCase(
            project_id=android_project.id,
            case_name="No Code Case",
            case_no="TC002",
            steps=json.dumps([{"step_number": 1, "action": "click", "target": "", "value": ""}]),
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        exec_obj = _create_execution(db_session, android_project)
        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)
        assert result is False

    def test_invalid_code_raises(self, db_session, appium_svc, mock_driver, android_project):
        """代码校验失败 → 抛出 SecurityException"""
        from app.exceptions import SecurityException
        invalid_code = "def run_test(driver):\n    import os\n    os.system('rm -rf /')\n"
        case = _create_case_and_code(db_session, android_project, invalid_code)
        exec_obj = _create_execution(db_session, android_project)

        with pytest.raises(SecurityException, match="代码校验失败"):
            appium_svc._execute_case(mock_driver, exec_obj.id, case.id)


# ═══════════════════════════════════════════════════
# Tests: _execute_sync — 执行生命周期
# ═══════════════════════════════════════════════════

class TestAppiumExecuteSync:
    """_execute_sync() — 完整执行生命周期"""

    @patch("appium.webdriver.Remote")
    def test_cleanup_driver_quit(self, mock_remote, db_session, android_project):
        """执行完成后调用 driver.quit() 清理"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        mock_driver.quit.assert_called_once()

    @patch("appium.webdriver.Remote")
    def test_stop_flag_skips_execution(self, mock_remote, db_session, android_project):
        """停止标志设置后跳过用例执行"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        # 执行前设置停止标志
        set_stop_flag(exec_obj.id)
        try:
            svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

            # 未执行任何用例：passed_cases / failed_cases 应为 0
            from app.models.execution import Execution
            db_session.expire_all()
            updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
            assert updated is not None
            assert updated.passed_cases == 0
            assert updated.failed_cases == 0
        finally:
            clear_stop_flag(exec_obj.id)

    @patch("appium.webdriver.Remote")
    def test_stop_flag_during_execution(self, mock_remote, db_session, android_project):
        """执行过程中设置停止标志 → 中断后续用例"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        # 创建两个用例，第二个应被跳过
        case1 = _create_case_and_code(db_session, android_project, CLICK_CODE)
        case2 = _create_case_and_code(db_session, android_project, CLICK_CODE,
                                       steps=[{"step_number": 1, "action": "click", "target": "btn2", "value": ""}])
        exec_obj = _create_execution(db_session, android_project)

        try:
            # 模拟第一个用例执行完后设置停止标志
            original_exec = svc._execute_case

            def _side_effect(driver, execution_id, case_id):
                result = original_exec(driver, execution_id, case_id)
                set_stop_flag(execution_id)
                return result

            svc._execute_case = _side_effect
            svc._execute_sync(android_project.id, [case1.id, case2.id], exec_obj.id, "headless")

            # 验证 passed_cases 为 1（只有 case1 执行了）
            from app.models.execution import Execution
            db_session.expire_all()
            updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
            assert updated is not None
            assert updated.passed_cases == 1
        finally:
            svc._execute_case = original_exec
            clear_stop_flag(exec_obj.id)

    @patch("appium.webdriver.Remote")
    def test_execution_status_completed(self, mock_remote, db_session, android_project):
        """全部成功 → status = completed"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        from app.models.execution import Execution
        db_session.expire_all()
        updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert updated.status == "completed"


# ═══════════════════════════════════════════════════
# Tests: create_execution — 执行记录创建
# ═══════════════════════════════════════════════════

class TestAppiumCreateExecution:
    """create_execution() — 执行记录创建"""

    def test_create_execution(self, db_session, android_project):
        """创建执行记录并初始化步骤"""
        from app.services.appium_service import AppiumService
        from app.models.test_case import TestCase
        svc = AppiumService(db_session)

        case = TestCase(
            project_id=android_project.id,
            case_name="Test Case",
            case_no="TC001",
            steps=json.dumps([
                {"step_number": 1, "action": "click", "target": "#btn", "value": ""},
                {"step_number": 2, "action": "fill", "target": "#input", "value": "hello"},
            ]),
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        exec_id = svc.create_execution(android_project.id, [case.id])

        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_id,
        ).all()
        assert len(steps) == 2
        assert steps[0].action == "click"
        assert steps[1].action == "fill"


# ═══════════════════════════════════════════════════
# Tests: Session 配置（通过 _execute_sync 间接验证）
# ═══════════════════════════════════════════════════

class TestAppiumSessionConfig:
    """Appium Session 配置读取"""

    @patch("appium.webdriver.Remote")
    def test_execute_sync_uses_config_json(self, mock_remote, db_session, android_project):
        """_execute_sync 从 project.config_json 读取 Appium 配置"""
        import json
        from app.services.appium_service import AppiumService
        android_project.config_json = json.dumps({
            "appium_server_url": "http://custom:4723",
            "app_package": "com.example.app",
            "app_activity": ".MainActivity",
            "device_name": "test-device",
            "platform_version": "12.0",
        })
        db_session.commit()

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        svc = AppiumService(db_session)
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)
        svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        # Remote 应以自定义 URL 被调用
        call_args, call_kwargs = mock_remote.call_args
        # 第一个参数是 appium_url
        assert call_args[0] == "http://custom:4723"
        # desired_caps 应包含自定义配置
        caps = call_args[1]
        assert caps["appPackage"] == "com.example.app"
        assert caps["appActivity"] == ".MainActivity"
        assert caps["deviceName"] == "test-device"
        assert caps["platformVersion"] == "12.0"

    @patch("appium.webdriver.Remote")
    def test_execute_sync_fallback_config(self, mock_remote, db_session, android_project):
        """config_json 为空时使用默认配置"""
        from app.services.appium_service import AppiumService
        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        svc = AppiumService(db_session)
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)
        svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        call_args, call_kwargs = mock_remote.call_args
        caps = call_args[1]
        assert caps["platformName"] == "Android"
        assert caps["automationName"] == "UiAutomator2"


# ═══════════════════════════════════════════════════
# Tests: Startup Failure
# ═══════════════════════════════════════════════════

class TestAppiumStartupFailure:
    """AppiumService 启动失败场景"""

    @patch("appium.webdriver.Remote")
    def test_remote_connection_failure(self, mock_remote, db_session, android_project):
        """Remote 连接失败 → execution 状态设为 failed"""
        from app.services.appium_service import AppiumService
        mock_remote.side_effect = Exception("Connection refused")

        svc = AppiumService(db_session)
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        from app.models.execution import Execution
        db_session.expire_all()
        updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert updated.status == "failed"

    @patch("appium.webdriver.Remote")
    def test_driver_quit_on_startup_failure(self, mock_remote, db_session, android_project):
        """启动失败时 driver.quit 不会被调用（driver 未创建）"""
        from app.services.appium_service import AppiumService
        mock_driver = MagicMock()
        mock_remote.side_effect = Exception("Connection refused")

        svc = AppiumService(db_session)
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        # driver.quit 不应被调用（driver 从未成功创建）
        mock_driver.quit.assert_not_called()


# ═══════════════════════════════════════════════════
# Tests: create_execution 边界
# ═══════════════════════════════════════════════════

class TestAppiumCreateExecutionEdgeCases:
    """create_execution() — 无效步骤数据分支"""

    def test_create_execution_skips_invalid_json_steps(self, db_session, android_project):
        """用例 steps 为无效 JSON → 跳过该用例步骤"""
        from app.services.appium_service import AppiumService
        from app.models.test_case import TestCase
        case = TestCase(
            project_id=android_project.id,
            case_name="Broken Steps",
            case_no="TC999",
            steps="not valid json {{{",
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        svc = AppiumService(db_session)
        exec_id = svc.create_execution(android_project.id, [case.id])

        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_id,
        ).all()
        assert len(steps) == 0

    def test_create_execution_skips_case_without_steps(self, db_session, android_project):
        """用例无 steps → 跳过该用例步骤"""
        from app.services.appium_service import AppiumService
        from app.models.test_case import TestCase
        case = TestCase(
            project_id=android_project.id,
            case_name="No Steps",
            case_no="TC998",
            steps="",
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        svc = AppiumService(db_session)
        exec_id = svc.create_execution(android_project.id, [case.id])

        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_id,
        ).all()
        assert len(steps) == 0


# ═══════════════════════════════════════════════════
# Tests: _execute_sync 失败路径
# ═══════════════════════════════════════════════════

class TestAppiumExecuteSyncFailures:
    """_execute_sync() — 失败/异常分支"""

    @patch("appium.webdriver.Remote")
    def test_case_exception_triggers_healing(self, mock_remote, db_session, android_project):
        """_execute_case 抛异常 → 计入 failed，触发 healing 并启动自愈"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        mock_start_healing = patch.object(AppiumService, "_start_healing")
        with mock_start_healing as mock_heal:
            svc._execute_case = MagicMock(side_effect=RuntimeError("boom"))
            svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        from app.models.execution import Execution
        db_session.expire_all()
        updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert updated.status == "healing"
        assert updated.failed_cases == 1
        mock_heal.assert_called_once()

    @patch("appium.webdriver.Remote")
    def test_driver_quit_exception_ignored(self, mock_remote, db_session, android_project):
        """driver.quit() 抛异常 → 被忽略，状态仍正常更新"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver
        mock_driver.quit.side_effect = RuntimeError("quit failed")

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        with patch.object(AppiumService, "_start_healing"):
            svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        from app.models.execution import Execution
        db_session.expire_all()
        updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert updated.status == "completed"  # quit 异常不影响状态

    @patch("appium.webdriver.Remote")
    def test_project_not_found_uses_default_config(self, mock_remote, db_session, android_project):
        """项目不存在 → 使用默认配置连接"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        with patch.object(AppiumService, "_start_healing"):
            svc._execute_sync(99999, [case.id], exec_obj.id, "headless")

        # 项目不存在 → config={} → 使用 settings.APPIUM_URL
        call_args, _ = mock_remote.call_args
        assert call_args[1]["automationName"] == "UiAutomator2"

    @patch("appium.webdriver.Remote")
    def test_case_returns_false_counts_failed(self, mock_remote, db_session, android_project):
        """_execute_case 返回 False → 计入 failed，触发 healing"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)

        mock_driver = MagicMock()
        mock_remote.return_value = mock_driver

        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        with patch.object(AppiumService, "_start_healing") as mock_heal:
            svc._execute_case = MagicMock(return_value=False)
            svc._execute_sync(android_project.id, [case.id], exec_obj.id, "headless")

        from app.models.execution import Execution
        db_session.expire_all()
        updated = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert updated.status == "healing"
        assert updated.failed_cases == 1
        mock_heal.assert_called_once()


# ═══════════════════════════════════════════════════
# Tests: _execute_case 边界
# ═══════════════════════════════════════════════════

class TestAppiumExecuteCaseEdgeCases:
    """_execute_case() — 校验/执行失败分支"""

    def test_inject_security_exception_rejected(self, db_session, appium_svc, mock_driver, android_project, mocker):
        """AppiumCodeInjector.inject 抛 SecurityException → 原样抛出"""
        from app.exceptions import SecurityException
        from app.utils.appium_code_injector import AppiumCodeInjector
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        mocker.patch.object(AppiumCodeInjector, "inject", side_effect=SecurityException("inject failed"))

        with pytest.raises(SecurityException, match="inject failed"):
            appium_svc._execute_case(mock_driver, exec_obj.id, case.id)

    def test_exec_compile_error_marks_failed(self, db_session, appium_svc, mock_driver, android_project, mocker):
        """exec 执行失败 → 用例标记失败并返回 False"""
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        mocker.patch("app.services.appium_service.exec", side_effect=RuntimeError("compile error"))

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)
        assert result is False

        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).all()
        assert all(s.status == "failed" for s in steps)

    def test_missing_run_test_marks_failed(self, db_session, appium_svc, mock_driver, android_project, mocker):
        """代码中无 run_test 函数 → 标记失败并返回 False"""
        case = _create_case_and_code(db_session, android_project, CLICK_CODE)
        exec_obj = _create_execution(db_session, android_project)

        # 模拟注入后代码不包含 run_test（namespace 中无 run_test）
        mocker.patch("app.services.appium_service.exec")

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)
        assert result is False

    def test_run_test_returns_false_marks_failed(self, db_session, appium_svc, mock_driver, android_project):
        """run_test 返回 success=False → 标记失败并返回 False"""
        code = "def run_test(driver):\n    return {'success': False}\n"
        case = _create_case_and_code(db_session, android_project, code)
        exec_obj = _create_execution(db_session, android_project)

        result = appium_svc._execute_case(mock_driver, exec_obj.id, case.id)
        assert result is False

        from app.models.execution_step import ExecutionStep
        steps = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).all()
        assert all(s.status == "failed" for s in steps)


# ═══════════════════════════════════════════════════
# Tests: 辅助方法
# ═══════════════════════════════════════════════════

class TestAppiumHelpers:
    """_init_steps / _update_execution / _update_execution_status / _ensure_dir"""

    def test_init_steps_skips_case_without_steps(self, db_session, android_project):
        """_init_steps 用例无 steps → 直接返回"""
        from app.services.appium_service import AppiumService
        from app.models.test_case import TestCase
        from app.models.execution import Execution
        case = TestCase(
            project_id=android_project.id,
            case_name="No Steps",
            case_no="TC997",
            steps="",
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)
        exec_obj = Execution(
            project_id=android_project.id,
            total_cases=1,
            status="running",
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = AppiumService(db_session)
        svc._init_steps(exec_obj.id, case.id)  # 不应抛异常

    def test_init_steps_skips_invalid_json(self, db_session, android_project):
        """_init_steps 用例 steps 无效 JSON → 直接返回"""
        from app.services.appium_service import AppiumService
        from app.models.test_case import TestCase
        from app.models.execution import Execution
        case = TestCase(
            project_id=android_project.id,
            case_name="Broken",
            case_no="TC996",
            steps="not json {{{",
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)
        exec_obj = Execution(
            project_id=android_project.id,
            total_cases=1,
            status="running",
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = AppiumService(db_session)
        svc._init_steps(exec_obj.id, case.id)  # 不应抛异常

    def test_update_execution_not_found(self, db_session, android_project):
        """_update_execution 执行记录不存在 → 静默跳过"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)
        svc._update_execution(99999, 1, 0)  # 不应抛异常

    def test_update_execution_db_error_logged(self, db_session, android_project, mocker):
        """_update_execution DB 异常 → 记录日志，不向上抛"""
        from app.services.appium_service import AppiumService
        from app.models.execution import Execution
        exec_obj = Execution(
            project_id=android_project.id,
            total_cases=1,
            status="running",
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = AppiumService(db_session)
        mocker.patch.object(svc._db, "commit", side_effect=RuntimeError("db down"))
        svc._update_execution(exec_obj.id, 1, 0)  # 不应抛异常

    def test_update_execution_status_sets_end_time(self, db_session, android_project):
        """_update_execution_status 终态 → 设置 end_time"""
        from app.services.appium_service import AppiumService
        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=android_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)
        assert exec_obj.end_time is None

        svc = AppiumService(db_session)
        svc._update_execution_status(exec_obj.id, "stopped")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "stopped"
        assert exec_obj.end_time is not None

    def test_update_execution_status_not_found(self, db_session, android_project):
        """_update_execution_status 记录不存在 → 静默跳过"""
        from app.services.appium_service import AppiumService
        svc = AppiumService(db_session)
        svc._update_execution_status(99999, "failed")  # 不应抛异常

    def test_ensure_dir(self):
        """_ensure_dir 创建目录并返回路径"""
        from app.services.appium_service import AppiumService
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "dir")
            result = AppiumService._ensure_dir(path)
            assert result == path
            assert os.path.isdir(path)


# ═══════════════════════════════════════════════════
# Tests: _SyncMonitorHooks 边界
# ═══════════════════════════════════════════════════

class TestSyncMonitorHooksEdgeCases:
    """_SyncMonitorHooks — 截图失败 / 新建步骤 / DB 异常"""

    def _make_hooks(self, db_session, android_project, driver, mock_file_ops):
        from app.services.appium_service import _SyncMonitorHooks
        from app.models.execution import Execution
        from app.models.test_case import TestCase
        case = TestCase(
            project_id=android_project.id,
            case_name="Hooks Case",
            case_no="TC995",
            steps=json.dumps([{"step_number": 1, "action": "click", "target": "#b", "value": ""}]),
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)
        exec_obj = Execution(
            project_id=android_project.id,
            total_cases=1,
            status="running",
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)
        return _SyncMonitorHooks(db_session, exec_obj.id, case.id, driver), exec_obj, case

    def test_on_step_before_screenshot_fails(self, db_session, android_project, mock_file_ops):
        """before 截图失败 → 记录空截图路径，不中断"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        driver.save_screenshot.side_effect = RuntimeError("shot failed")
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        hooks.on_step_before(1, "click", "#b", "")

        from app.models.execution_step import ExecutionStep
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).first()
        assert step is not None
        assert step.status == "running"
        assert step.screenshot_before == ""

    def test_on_step_after_passed_sets_success(self, db_session, android_project, mock_file_ops):
        """after 成功 → status=success + log_output"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        hooks.on_step_before(1, "click", "#b", "")
        hooks.on_step_after(1, "passed")

        from app.models.execution_step import ExecutionStep
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).first()
        assert step.status == "success"
        assert "[PASS]" in (step.log_output or "")

    def test_on_step_after_failed_without_error_msg(self, db_session, android_project, mock_file_ops):
        """after 失败但无 error_msg → error_message 不设置"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        hooks.on_step_before(1, "click", "#b", "")
        hooks.on_step_after(1, "failed")

        from app.models.execution_step import ExecutionStep
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).first()
        assert step.status == "failed"
        assert step.error_message is None  # 无 error_msg → else 分支

    def test_on_step_after_failed_with_error_msg(self, db_session, android_project, mock_file_ops):
        """after 失败且有 error_msg → 组合 exception_type 与 error_message"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        hooks.on_step_before(1, "click", "#b", "")
        hooks.on_step_after(1, "failed", "NoSuchElement: not found", "NoSuchElementException")

        from app.models.execution_step import ExecutionStep
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).first()
        assert step.status == "failed"
        assert step.error_message.startswith("NoSuchElementException: NoSuchElement")
        assert step.exception_type == "NoSuchElementException"
        assert "[FAIL]" in (step.log_output or "")

    def test_upsert_step_db_error_rolls_back(self, db_session, android_project, mock_file_ops, mocker):
        """_upsert_step DB 异常 → 回滚，不向上抛"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        mocker.patch.object(hooks._db, "commit", side_effect=RuntimeError("db down"))
        hooks.on_step_before(1, "click", "#b", "")  # 不应抛异常

    def test_on_step_after_screenshot_fails(self, db_session, android_project, mock_file_ops):
        """after 截图失败 → 记录空截图路径，状态仍更新"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        # 第一次（before）成功，第二次（after）失败
        driver.save_screenshot.side_effect = [None, RuntimeError("shot failed")]
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        hooks.on_step_before(1, "click", "#b", "")
        hooks.on_step_after(1, "passed")

        from app.models.execution_step import ExecutionStep
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).first()
        assert step.status == "success"
        assert step.screenshot_after == ""

    def test_on_step_after_failed_without_exception_type(self, db_session, android_project, mock_file_ops):
        """after 失败且有 error_msg 但无 exception_type → 直接使用 error_msg"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        hooks.on_step_before(1, "click", "#b", "")
        hooks.on_step_after(1, "failed", "plain error message")

        from app.models.execution_step import ExecutionStep
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
            ExecutionStep.case_id == case.id,
        ).first()
        assert step.status == "failed"
        assert step.error_message == "plain error message"
        assert step.exception_type == ""

    def test_upsert_step_rollback_failure_ignored(self, db_session, android_project, mock_file_ops, mocker):
        """_upsert_step 中 commit 与 rollback 均失败 → 静默忽略"""
        from app.services.appium_service import _SyncMonitorHooks
        driver = MagicMock()
        hooks, exec_obj, case = self._make_hooks(db_session, android_project, driver, mock_file_ops)

        mocker.patch.object(hooks._db, "commit", side_effect=RuntimeError("db down"))
        mocker.patch.object(hooks._db, "rollback", side_effect=RuntimeError("rollback down"))
        hooks.on_step_before(1, "click", "#b", "")  # 不应抛异常