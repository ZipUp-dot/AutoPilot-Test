"""Playwright 安全执行引擎单元测试"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.playwright_service import (
    PlaywrightService,
    _MonitorHooks,
    _build_namespace,
)
from app.services.execution_state import set_stop_flag, clear_stop_flag
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep


# ═══════════════════════════════════════════════
# create_execution() 测试
# ═══════════════════════════════════════════════

class TestCreateExecution:
    """创建执行记录测试"""

    def test_create_execution_returns_id(self, db_session, sample_project, sample_test_case):
        """场景1: create_execution() -> 返回 execution_id, Execution 记录创建"""
        svc = PlaywrightService(db_session)
        exec_id = svc.create_execution(
            project_id=sample_project.id,
            case_ids=[sample_test_case.id],
            mode="headless",
            batch_name="Test Batch",
        )

        assert exec_id is not None
        assert exec_id > 0

        # 验证 Execution 记录
        exec_obj = db_session.query(Execution).filter(Execution.id == exec_id).first()
        assert exec_obj is not None
        assert exec_obj.project_id == sample_project.id
        assert exec_obj.batch_name == "Test Batch"
        assert exec_obj.total_cases == 1
        assert exec_obj.status == "running"
        assert exec_obj.execution_mode == "headless"

    def test_create_execution_creates_steps(self, db_session, sample_project, sample_test_case):
        """场景2: create_execution() 为每个步骤创建 ExecutionStep 记录"""
        svc = PlaywrightService(db_session)
        exec_id = svc.create_execution(
            project_id=sample_project.id,
            case_ids=[sample_test_case.id],
        )

        steps = (
            db_session.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == exec_id)
            .all()
        )
        assert len(steps) == 2  # sample_test_case has 2 steps
        assert steps[0].status == "pending"
        assert steps[0].step_index == 1
        assert steps[0].action == "navigate"
        assert steps[1].step_index == 2
        assert steps[1].action == "fill"

    def test_create_execution_invalid_case_skipped(self, db_session, sample_project):
        """场景3: create_execution() 无效 case_id -> 跳过，不崩溃"""
        svc = PlaywrightService(db_session)
        exec_id = svc.create_execution(
            project_id=sample_project.id,
            case_ids=[99999],  # 不存在的 case
        )

        # 不应崩溃，execution 记录仍创建
        exec_obj = db_session.query(Execution).filter(Execution.id == exec_id).first()
        assert exec_obj is not None
        assert exec_obj.total_cases == 1

        # 没有步骤记录
        steps = (
            db_session.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == exec_id)
            .all()
        )
        assert len(steps) == 0


# ═══════════════════════════════════════════════
# _init_steps() 测试
# ═══════════════════════════════════════════════

class TestInitSteps:
    """初始化步骤记录测试"""

    def test_init_steps_creates_pending_records(self, db_session, sample_project, sample_test_case):
        """场景4: _init_steps() -> 创建 status='pending' 的 ExecutionStep 记录"""
        from app.services.playwright_service import PlaywrightService
        from app.models.execution import Execution
        from datetime import datetime as dt

        # 先创建 execution
        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        svc._init_steps(exec_obj.id, sample_test_case.id)

        steps = (
            db_session.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == exec_obj.id,
                ExecutionStep.case_id == sample_test_case.id,
            )
            .order_by(ExecutionStep.step_index)
            .all()
        )
        assert len(steps) == 2
        for s in steps:
            assert s.status == "pending"

    def test_init_steps_no_case(self, db_session, sample_project):
        """_init_steps() 不存在的 case -> 不崩溃"""
        from app.services.playwright_service import PlaywrightService
        from app.models.execution import Execution
        from datetime import datetime as dt

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()

        svc = PlaywrightService(db_session)
        svc._init_steps(exec_obj.id, 99999)  # 不存在的 case，不应崩溃


# ═══════════════════════════════════════════════
# _update_execution() 测试
# ═══════════════════════════════════════════════

class TestUpdateExecution:
    """更新执行记录测试"""

    def test_update_execution_updates_counts(self, db_session, sample_project):
        """场景5: _update_execution() -> passed_cases, failed_cases 更新"""
        from app.models.execution import Execution
        from datetime import datetime as dt

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=3,
            passed_cases=0,
            failed_cases=0,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        svc._update_execution(exec_obj.id, passed=2, failed=1)

        db_session.refresh(exec_obj)
        assert exec_obj.passed_cases == 2
        assert exec_obj.failed_cases == 1


# ═══════════════════════════════════════════════
# _update_execution_status() 测试
# ═══════════════════════════════════════════════

class TestUpdateExecutionStatus:
    """更新执行状态测试"""

    def test_update_execution_status(self, db_session, sample_project):
        """场景6: _update_execution_status() -> status + end_time 更新"""
        from app.models.execution import Execution
        from datetime import datetime as dt

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        svc._update_execution_status(exec_obj.id, "completed")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "completed"
        assert exec_obj.end_time is not None


# ═══════════════════════════════════════════════
# 停止标志测试
# ═══════════════════════════════════════════════

class TestStopFlag:
    """停止标志测试"""

    def test_set_stop_flag_and_check(self, db_session, sample_project):
        """场景7: set_stop_flag() / _is_stopped() -> 标志设置，检测生效"""
        from app.models.execution import Execution
        from datetime import datetime as dt

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)

        # 初始未停止
        assert svc._is_stopped(exec_obj.id) is False

        # 设置停止标志
        set_stop_flag(exec_obj.id)
        assert svc._is_stopped(exec_obj.id) is True

    def test_clear_stop_flag(self, db_session, sample_project):
        """场景8: clear_stop_flag() -> 标志移除"""
        from app.models.execution import Execution
        from datetime import datetime as dt

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)

        set_stop_flag(exec_obj.id)
        assert svc._is_stopped(exec_obj.id) is True

        clear_stop_flag(exec_obj.id)
        assert svc._is_stopped(exec_obj.id) is False


# ═══════════════════════════════════════════════
# _MonitorHooks 测试
# ═══════════════════════════════════════════════

class TestMonitorHooks:
    """监控钩子测试"""

    @pytest.mark.asyncio
    async def test_on_step_before_creates_record(self, db_session, sample_execution, sample_test_case, mock_file_ops):
        """场景9: _MonitorHooks.on_step_before() -> 创建 step 记录 status='running', screenshot 路径设置"""
        from unittest.mock import AsyncMock
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b"fake"

        hooks = _MonitorHooks(
            db_session, sample_execution.id, sample_test_case.id, mock_page
        )
        await hooks.on_step_before(1, "click", "#btn", "")

        step = (
            db_session.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == sample_execution.id,
                ExecutionStep.step_index == 1,
            )
            .first()
        )
        assert step is not None
        assert step.status == "running"
        assert step.action == "click"
        assert step.target_selector == "#btn"
        assert step.screenshot_before is not None

    @pytest.mark.asyncio
    async def test_on_step_after_success(self, db_session, sample_execution, sample_test_case, mock_file_ops):
        """场景10: _MonitorHooks.on_step_after() 成功 -> step status='success', duration 记录"""
        from unittest.mock import AsyncMock
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b"fake"

        hooks = _MonitorHooks(
            db_session, sample_execution.id, sample_test_case.id, mock_page
        )
        # 先 before
        await hooks.on_step_before(1, "click", "#btn", "")
        # 然后 after 成功
        await hooks.on_step_after(1, "passed", "")

        step = (
            db_session.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == sample_execution.id,
                ExecutionStep.step_index == 1,
            )
            .first()
        )
        assert step is not None
        assert step.status == "success"
        assert step.duration_ms is not None
        assert step.duration_ms >= 0
        assert step.screenshot_after is not None

    @pytest.mark.asyncio
    async def test_on_step_after_failure(self, db_session, sample_execution, sample_test_case, mock_file_ops):
        """场景11: _MonitorHooks.on_step_after() 失败 -> step status='failed', error_message 记录"""
        from unittest.mock import AsyncMock
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b"fake"

        hooks = _MonitorHooks(
            db_session, sample_execution.id, sample_test_case.id, mock_page
        )
        await hooks.on_step_before(1, "click", "#btn", "")
        await hooks.on_step_after(1, "failed", "Element not found")

        step = (
            db_session.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == sample_execution.id,
                ExecutionStep.step_index == 1,
            )
            .first()
        )
        assert step is not None
        assert step.status == "failed"
        assert step.error_message is not None
        assert "Element not found" in step.error_message


# ═══════════════════════════════════════════════
# _build_namespace() 测试
# ═══════════════════════════════════════════════

class TestBuildNamespace:
    """命名空间构建测试"""

    def test_build_namespace_contains_key_modules(self):
        """场景12: _build_namespace() -> 包含 page, json, asyncio, monitor hooks, datetime"""
        import asyncio
        from datetime import datetime
        from unittest.mock import AsyncMock

        mock_page = AsyncMock()
        mock_hooks = MagicMock()

        ns = _build_namespace(mock_page, mock_hooks)

        assert ns["page"] is mock_page
        assert ns["json"] is json
        assert ns["asyncio"] is asyncio
        assert ns["datetime"] is datetime
        assert ns["__monitor_before"] is not None
        assert ns["__monitor_after"] is not None
        assert isinstance(ns["__builtins__"], dict)

    def test_build_namespace_banned_builtins_excluded(self):
        """场景13: _build_namespace() 禁止的 builtins 排除 -> eval, exec, open 不在命名空间"""
        from unittest.mock import AsyncMock

        mock_page = AsyncMock()
        mock_hooks = MagicMock()

        ns = _build_namespace(mock_page, mock_hooks)
        builtins = ns["__builtins__"]

        # 危险内置函数不应存在
        assert "eval" not in builtins
        assert "exec" not in builtins
        assert "open" not in builtins
        assert "compile" not in builtins

    def test_build_namespace_safe_builtins_included(self):
        """场景14: _build_namespace() 安全 builtins 包含 -> print, len, str, range 等"""
        from unittest.mock import AsyncMock

        mock_page = AsyncMock()
        mock_hooks = MagicMock()

        ns = _build_namespace(mock_page, mock_hooks)
        builtins = ns["__builtins__"]

        assert "len" in builtins
        assert "str" in builtins
        assert "range" in builtins
        assert "int" in builtins
        assert "float" in builtins
        assert "bool" in builtins
        assert "list" in builtins
        assert "dict" in builtins
        assert "tuple" in builtins
        assert "set" in builtins
        assert "isinstance" in builtins
        assert "enumerate" in builtins
        assert "zip" in builtins
        assert "sorted" in builtins
        assert "min" in builtins
        assert "max" in builtins
        assert "True" in builtins
        assert "False" in builtins
        assert "Exception" in builtins


# ═══════════════════════════════════════════════
# _execute_case() 测试
# ═══════════════════════════════════════════════

class TestExecuteCase:
    """单条用例执行（沙箱执行核心）测试"""

    @pytest.mark.asyncio
    async def test_execute_case_success(self, db_session, sample_test_case, sample_generated_code, mocker):
        """场景15: _execute_case() 成功执行 → 返回 True"""
        mocker.patch("app.services.playwright_service.CodeValidator.validate", return_value=None)
        mocker.patch("app.services.playwright_service.CodeInjector.inject",
                      return_value=sample_generated_code.code_content)
        mocker.patch("pathlib.Path.mkdir")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)
        result = await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)
        assert result is True

    @pytest.mark.asyncio
    async def test_execute_case_no_generated_code(self, db_session, sample_test_case, mocker):
        """场景16: _execute_case() 无生成代码 → 返回 False"""
        mocker.patch("pathlib.Path.mkdir")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)
        result = await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_case_validation_fails(self, db_session, sample_test_case, sample_generated_code, mocker):
        """场景17: _execute_case() 代码校验失败 → 抛出 SecurityException"""
        mocker.patch("app.services.playwright_service.CodeValidator.validate",
                      return_value="dangerous code detected")
        mocker.patch("pathlib.Path.mkdir")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)

        from app.exceptions import SecurityException
        with pytest.raises(SecurityException):
            await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)

    @pytest.mark.asyncio
    async def test_execute_case_exec_compile_error(self, db_session, sample_test_case, sample_generated_code, mocker):
        """场景18: _execute_case() exec 编译失败 → 返回 False"""
        mocker.patch("app.services.playwright_service.CodeValidator.validate", return_value=None)
        # 注入无效代码使 exec 编译失败
        mocker.patch("app.services.playwright_service.CodeInjector.inject",
                      return_value="def broken( {{{")
        mocker.patch("pathlib.Path.mkdir")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)
        result = await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_case_missing_run_test(self, db_session, sample_test_case, sample_generated_code, mocker):
        """场景19: _execute_case() exec 后 namespace 缺少 run_test → 返回 False"""
        mocker.patch("app.services.playwright_service.CodeValidator.validate", return_value=None)
        # 注入不定义 run_test 的代码
        mocker.patch("app.services.playwright_service.CodeInjector.inject",
                      return_value="x = 1")
        mocker.patch("pathlib.Path.mkdir")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)
        result = await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_case_run_test_returns_false(self, db_session, sample_test_case, sample_generated_code, mocker):
        """场景20: _execute_case() run_test 返回 success=False → 返回 False"""
        mocker.patch("app.services.playwright_service.CodeValidator.validate", return_value=None)
        # 注入返回 success=False 的代码
        mocker.patch("app.services.playwright_service.CodeInjector.inject",
                      return_value="async def run_test(page):\n    return {'success': False}")
        mocker.patch("pathlib.Path.mkdir")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)
        result = await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_case_timeout(self, db_session, sample_test_case, sample_generated_code, mocker):
        """场景21: _execute_case() 执行超时 → 返回 False"""
        mocker.patch("app.services.playwright_service.CodeValidator.validate", return_value=None)
        mocker.patch("app.services.playwright_service.CodeInjector.inject",
                      return_value=sample_generated_code.code_content)
        mocker.patch("pathlib.Path.mkdir")

        # mock asyncio.wait_for 时先关闭传入的 run_test(page) 协程，
        # 否则该协程从未被 await 会触发 RuntimeWarning
        def _mock_wait_for(coro, *args, **kwargs):
            coro.close()
            raise asyncio.TimeoutError("timeout")

        mocker.patch("asyncio.wait_for", side_effect=_mock_wait_for)

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_test_case.project_id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        mock_page = AsyncMock()
        svc = PlaywrightService(db_session)
        result = await svc._execute_case(mock_page, exec_obj.id, sample_test_case.id)
        assert result is False


# ═══════════════════════════════════════════════
# _execute_async() 批量执行测试
# ═══════════════════════════════════════════════

class TestExecuteAsync:
    """批量异步执行测试"""

    @pytest.mark.asyncio
    async def test_execute_async_all_pass(self, db_session, sample_project, sample_test_case, sample_generated_code, mocker):
        """场景22: _execute_async() 全部用例通过 → status='completed'"""
        mock_playwright_for_execution_service_func(mocker)
        mocker.patch.object(PlaywrightService, "_execute_case", return_value=True)
        mocker.patch.object(PlaywrightService, "_start_healing")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        await svc._execute_async(sample_project.id, [sample_test_case.id], exec_obj.id, "headless")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "completed"
        assert exec_obj.passed_cases == 1
        assert exec_obj.failed_cases == 0

    @pytest.mark.asyncio
    async def test_execute_async_with_failures(self, db_session, sample_project, sample_test_case, sample_generated_code, mocker):
        """场景23: _execute_async() 有用例失败 → status='healing'，触发自愈"""
        mock_playwright_for_execution_service_func(mocker)
        mocker.patch.object(PlaywrightService, "_execute_case", return_value=False)
        mock_heal = mocker.patch.object(PlaywrightService, "_start_healing")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        await svc._execute_async(sample_project.id, [sample_test_case.id], exec_obj.id, "headless")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "healing"
        assert exec_obj.failed_cases == 1
        mock_heal.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_async_stop_flag(self, db_session, sample_project, sample_test_case, sample_generated_code, mocker):
        """场景24: _execute_async() 停止标志置位 → 中断循环"""
        mock_playwright_for_execution_service_func(mocker)
        mocker.patch.object(PlaywrightService, "_execute_case", return_value=True)
        mocker.patch.object(PlaywrightService, "_start_healing")

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=3,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        # 设置停止标志
        set_stop_flag(exec_obj.id)

        svc = PlaywrightService(db_session)
        await svc._execute_async(sample_project.id, [sample_test_case.id, sample_test_case.id, sample_test_case.id],
                                 exec_obj.id, "headless")

        # _execute_case 不应被调用（循环在第一次迭代前就中断了）
        PlaywrightService._execute_case.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_async_browser_launch_fails(self, db_session, sample_project, sample_test_case, mocker):
        """场景25: _execute_async() 浏览器启动失败 → status='failed'"""
        # 自定义 playwright mock：启动时抛出异常
        mock_pw = mocker.AsyncMock()
        mock_pw.chromium.launch.side_effect = RuntimeError("Browser launch failed")

        mocker.patch(
            "playwright.async_api.async_playwright",
            return_value=mocker.AsyncMock(
                __aenter__=mocker.AsyncMock(return_value=mock_pw),
                __aexit__=mocker.AsyncMock(return_value=False),
            ),
        )

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        await svc._execute_async(sample_project.id, [sample_test_case.id], exec_obj.id, "headless")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "failed"


# ═══════════════════════════════════════════════
# execute() 入口异常处理测试
# ═══════════════════════════════════════════════

class TestExecuteEntry:
    """execute() 同步入口异常处理测试"""

    def test_execute_exception_sets_failed(self, db_session, sample_project, mocker):
        """场景26: execute() 中 _execute_async 异常 → 更新 status='failed'"""
        mocker.patch.object(PlaywrightService, "_execute_async", side_effect=RuntimeError("boom"))

        from app.models.execution import Execution
        from datetime import datetime as dt
        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        svc.execute(sample_project.id, [1], exec_obj.id, "headless")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "failed"


# ═══════════════════════════════════════════════
# _MonitorHooks 错误处理测试
# ═══════════════════════════════════════════════

class TestMonitorHooksErrorHandling:
    """监控钩子错误处理测试"""

    @pytest.mark.asyncio
    async def test_upsert_step_db_commit_error(self, db_session, sample_execution, sample_test_case, mocker):
        """场景27: _MonitorHooks._upsert_step() DB 异常 → 回滚，不崩溃"""
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b"fake"

        hooks = _MonitorHooks(db_session, sample_execution.id, sample_test_case.id, mock_page)

        # 模拟 commit 失败
        mocker.patch.object(db_session, "commit", side_effect=Exception("DB error"))
        # 使用 patch 而非 spy：spy 会真实执行 rollback，回滚外层事务导致 teardown 报错
        mock_rollback = mocker.patch.object(db_session, "rollback")

        # 不应抛出异常
        await hooks.on_step_before(1, "click", "#btn", "")
        mock_rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_step_before_screenshot_fails(self, db_session, sample_execution, sample_test_case, mocker):
        """场景28: _MonitorHooks.on_step_before() 截图失败 → 不崩溃，状态仍为 running"""
        mock_page = AsyncMock()
        mock_page.screenshot.side_effect = RuntimeError("Screenshot failed")

        hooks = _MonitorHooks(db_session, sample_execution.id, sample_test_case.id, mock_page)
        # 不应抛出异常
        await hooks.on_step_before(1, "click", "#btn", "")

        step = (
            db_session.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == sample_execution.id,
                ExecutionStep.step_index == 1,
            )
            .first()
        )
        assert step is not None
        assert step.status == "running"
        assert step.screenshot_before == ""

    @pytest.mark.asyncio
    async def test_on_step_after_screenshot_fails(self, db_session, sample_execution, sample_test_case, mocker):
        """场景29: _MonitorHooks.on_step_after() 截图失败 → 不崩溃，状态仍更新"""
        mock_page = AsyncMock()
        mock_page.screenshot.side_effect = RuntimeError("Screenshot failed")

        hooks = _MonitorHooks(db_session, sample_execution.id, sample_test_case.id, mock_page)
        await hooks.on_step_before(1, "click", "#btn", "")
        # after 截图失败，不应崩溃
        await hooks.on_step_after(1, "passed", "")

        step = (
            db_session.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == sample_execution.id,
                ExecutionStep.step_index == 1,
            )
            .first()
        )
        assert step is not None
        assert step.status == "success"
        assert step.screenshot_after == ""


# ═══════════════════════════════════════════════
# _build_namespace() 边界测试
# ═══════════════════════════════════════════════

class TestBuildNamespaceEdgeCases:
    """命名空间构建边界测试"""

    def test_build_namespace_contains_all_allowed_builtins(self):
        """场景30: _build_namespace() 验证所有 ALLOWED_BUILTINS 常量都被包含"""
        from unittest.mock import AsyncMock
        from app.services.playwright_service import ALLOWED_BUILTINS

        mock_page = AsyncMock()
        mock_hooks = MagicMock()

        ns = _build_namespace(mock_page, mock_hooks)
        builtins = ns["__builtins__"]

        # "None" 在 ALLOWED_BUILTINS 中但 getattr(builtins, "None") 返回 None 单例，
        # 被 _build_namespace 的 `if obj is not None` 排除，因此跳过验证
        for name in ALLOWED_BUILTINS:
            if name == "None":
                continue
            assert name in builtins, f"ALLOWED_BUILTINS 中的 '{name}' 应在 namespace 中"


# ═══════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════

def mock_playwright_for_execution_service_func(mocker):
    """内联 Playwright mock（供 _execute_async 测试使用）"""
    mock_page = mocker.AsyncMock()
    mock_page.goto.return_value = None
    mock_page.screenshot.return_value = b"fake_image"
    # set_default_timeout 是 Playwright 同步方法，必须用 MagicMock 而非 AsyncMock
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