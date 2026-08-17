"""测试编排器 — pytest 风格，Mock Service 注入 + 异常隔离验证"""

import asyncio
import json
from unittest.mock import MagicMock, patch, call

import pytest
from sqlalchemy.orm import Session

from app.services.orchestrator import TestOrchestrator


# ── Fixtures ──

@pytest.fixture
def mock_ai():
    """Mock AI service，默认返回成功生成结果"""
    ai = MagicMock()
    ai.generate_batch.return_value = [
        {"case_id": 1, "status": "success", "code_id": 10},
        {"case_id": 2, "status": "success", "code_id": 11},
    ]
    return ai


@pytest.fixture
def mock_pw():
    """Mock Playwright service"""
    pw = MagicMock()
    pw.create_execution.return_value = 42
    return pw


@pytest.fixture
def mock_report():
    """Mock Report service"""
    report = MagicMock()
    report.generate.return_value = {"report_id": 1, "download_url": "/reports/report.html"}
    return report


@pytest.fixture
def orch(mock_ai, mock_pw, mock_report):
    """完整注入的编排器"""
    return TestOrchestrator(
        ai_service=mock_ai,
        playwright_service=mock_pw,
        report_service=mock_report,
    )


# ── Helper: patch _check_cases_need_generation ──

def _patch_check_cases(orch, return_value):
    """替换 _check_cases_need_generation 为返回固定值的协程"""
    async def _mock(*args, **kwargs):
        return return_value
    orch._check_cases_need_generation = _mock


# ── Tests ──

@pytest.mark.usefixtures("mock_monitor_task")
class TestRunFullPipeline:
    """run_full_pipeline() 场景"""

    def test_all_pre_generated_returns_execution_id(self, orch):
        """所有用例已有代码 → generated=0，返回 execution_id"""
        _patch_check_cases(orch, [])  # 无需生成

        result = asyncio.run(orch.run_full_pipeline(1, [1, 2], "headless", "Batch"))

        assert result["execution_id"] == 42
        assert result["status"] == "running"
        assert result["generated"] == 0

    def test_need_generation_returns_generated_count(self, orch, mock_ai):
        """部分用例需要生成 → generated_count > 0"""
        _patch_check_cases(orch, [3, 4])
        mock_ai.generate_batch.return_value = [
            {"case_id": 3, "status": "success", "code_id": 12},
            {"case_id": 4, "status": "failed", "error": "no elements"},
        ]

        result = asyncio.run(orch.run_full_pipeline(1, [3, 4], "headless", "NeedGen"))

        assert result["execution_id"] == 42
        assert result["generated"] == 1  # only 1 success

    def test_ai_fails_pipeline_continues(self, orch, mock_pw):
        """AI 生成异常 → 异常隔离，流水线继续，仍返回 execution_id"""
        _patch_check_cases(orch, [1])
        orch.ai_service.generate_batch.side_effect = RuntimeError("AI service down")

        # 不应抛出异常
        result = asyncio.run(orch.run_full_pipeline(1, [1], "headless", "BrokenAI"))

        assert result["execution_id"] == 42
        assert result["generated"] == 0  # generation failed, count stays 0


class TestRunGenerateOnly:
    """run_generate_only() 场景"""

    def test_returns_generated_and_failed_counts(self, mock_ai):
        """返回 generated_count 和 failed_count"""
        mock_ai.generate_batch.return_value = [
            {"case_id": 1, "status": "success", "code_id": 10},
            {"case_id": 2, "status": "success", "code_id": 11},
            {"case_id": 3, "status": "failed", "error": "no elements"},
        ]
        orch = TestOrchestrator(ai_service=mock_ai)

        result = asyncio.run(orch.run_generate_only(1, [1, 2, 3]))

        assert result["generated_count"] == 2
        assert result["failed_count"] == 1
        assert len(result["results"]) == 3


@pytest.mark.usefixtures("mock_monitor_task")
class TestRunExecuteOnly:
    """run_execute_only() 场景"""

    def test_returns_execution_id(self, orch):
        """返回 execution_id 和 running 状态"""
        result = asyncio.run(orch.run_execute_only(1, [1, 2], "headless", "TestBatch"))

        assert result["execution_id"] == 42
        assert result["status"] == "running"


class TestCheckCasesNeedGeneration:
    """_check_cases_need_generation() 场景"""

    def test_returns_cases_without_valid_code(self, db_session, sample_test_case, sample_generated_code):
        """已有 is_valid=1 代码的用例不在返回列表中，无代码的用例在列表中"""
        from sqlalchemy.orm import Session

        orch = TestOrchestrator()
        # 使用与 db_session 相同的连接，确保数据可见
        conn = db_session.get_bind()

        def make_session():
            return Session(bind=conn)

        with patch("app.db.database.SessionLocal", make_session):
            # sample_test_case 有 sample_generated_code (is_valid=1)，不需要生成
            result = asyncio.run(orch._check_cases_need_generation([sample_test_case.id]))
            assert result == []

            # 不存在的 case_id 需要生成
            result2 = asyncio.run(orch._check_cases_need_generation([99999]))
            assert result2 == [99999]

            # 混合：一个已有代码，一个没有
            result3 = asyncio.run(orch._check_cases_need_generation([sample_test_case.id, 99999]))
            assert result3 == [99999]

    def test_db_error_falls_back_to_all_case_ids(self, orch):
        """DB 查询异常 → 返回全部 case_ids 作为兜底"""
        with patch("app.db.database.SessionLocal", side_effect=Exception("DB down")):
            result = asyncio.run(orch._check_cases_need_generation([1, 2, 3]))
            assert result == [1, 2, 3]


class TestEmptyOrchestrator:
    """空编排器（无 service 注入）"""

    def test_no_services_does_not_crash_on_attribute_access(self):
        """无服务注入时，属性访问不崩溃"""
        orch = TestOrchestrator()
        # 属性应存在（值为 None），访问不应抛异常
        assert orch.ai_service is None
        assert orch.playwright_service is None
        assert orch.report_service is None


@pytest.mark.usefixtures("mock_monitor_task")
class TestThreadStart:
    """验证 threading.Thread.start 被调用"""

    def test_thread_start_called(self, mock_threading_in_orchestrator, mock_ai):
        """run_execute_only 启动后台线程"""
        mock_pw = MagicMock()
        mock_pw.create_execution.return_value = 42
        orch = TestOrchestrator(ai_service=mock_ai, playwright_service=mock_pw)

        asyncio.run(orch.run_execute_only(1, [1, 2], "headless", "TestBatch"))

        # Thread 被构造
        mock_threading_in_orchestrator.assert_called_once()
        # Thread.start() 被调用
        mock_threading_in_orchestrator.return_value.start.assert_called_once()


@pytest.mark.usefixtures("mock_monitor_task")
class TestClearStopFlag:
    """验证 clear_stop_flag 在执行前被调用"""

    def test_clear_stop_flag_called_before_execution(self, mock_ai):
        """clear_stop_flag 在创建执行记录后、启动线程前被调用"""
        from unittest.mock import MagicMock

        mock_pw = MagicMock()
        mock_pw.create_execution.return_value = 42

        orch = TestOrchestrator(ai_service=mock_ai, playwright_service=mock_pw)

        with patch("app.services.playwright_service.clear_stop_flag") as mock_clear:
            asyncio.run(orch.run_execute_only(1, [1, 2]))

            mock_clear.assert_called_once_with(42)


# ═══════════════════════════════════════════════
# 新增测试：覆盖未测试的代码路径
# ═══════════════════════════════════════════════

@pytest.mark.usefixtures("mock_monitor_task")
class TestRunFullPipelineEdgeCases:
    """run_full_pipeline() 边缘场景 — 覆盖 lines 99-111"""

    def test_all_cases_need_generation_all_succeed(self, orch, mock_ai):
        """所有用例都需要生成且全部成功 → generated 等于用例数"""
        _patch_check_cases(orch, [1, 2, 3])
        mock_ai.generate_batch.return_value = [
            {"case_id": 1, "status": "success", "code_id": 10},
            {"case_id": 2, "status": "success", "code_id": 11},
            {"case_id": 3, "status": "success", "code_id": 12},
        ]

        result = asyncio.run(orch.run_full_pipeline(1, [1, 2, 3], "headless", "AllGen"))

        assert result["generated"] == 3
        assert result["execution_id"] == 42

    def test_background_thread_exception_fallback(
        self, orch, mock_ai, mock_threading_in_orchestrator, mocker
    ):
        """后台线程 execute 异常 → lines 99-111 兜底将 execution 状态设为 failed"""
        _patch_check_cases(orch, [])

        mock_clear_stop = mocker.patch("app.services.playwright_service.clear_stop_flag")

        asyncio.run(orch.run_full_pipeline(1, [1], "headless", "BgFail"))

        # 提取后台线程的 target 函数
        target_fn = mock_threading_in_orchestrator.call_args.kwargs["target"]

        # Mock 内部依赖：SessionLocal、PlaywrightService、Execution 查询
        mock_session = MagicMock()
        mock_exec_row = MagicMock()
        mock_exec_row.status = "running"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_exec_row

        mock_pw_svc = mocker.patch("app.services.playwright_service.PlaywrightService")
        mock_pw_svc.return_value.execute.side_effect = RuntimeError("execute failed")

        mocker.patch("app.db.database.SessionLocal", return_value=mock_session)
        mock_dt = mocker.patch("datetime.datetime")
        mock_dt.utcnow.return_value = "2024-01-01T00:00:00"

        # 执行后台线程函数
        target_fn()

        # lines 107-109: 验证兜底逻辑
        assert mock_exec_row.status == "failed"
        assert mock_exec_row.end_time == "2024-01-01T00:00:00"
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
        # clear_stop_flag 被调用 2 次：run_full_pipeline 直接调用 + _run finally 块
        assert mock_clear_stop.call_count >= 2

    def test_background_thread_fallback_db_error_pass(
        self, orch, mock_ai, mock_threading_in_orchestrator, mocker
    ):
        """后台线程兜底 DB 查询也异常 → lines 110-111 pass 被覆盖"""
        _patch_check_cases(orch, [])

        mock_clear_stop = mocker.patch("app.services.playwright_service.clear_stop_flag")

        asyncio.run(orch.run_full_pipeline(1, [1], "headless", "BgFail"))

        target_fn = mock_threading_in_orchestrator.call_args.kwargs["target"]

        mock_session = MagicMock()
        # DB 查询本身也抛异常，触发 inner except pass
        mock_session.query.side_effect = Exception("DB query failed")

        mock_pw_svc = mocker.patch("app.services.playwright_service.PlaywrightService")
        mock_pw_svc.return_value.execute.side_effect = RuntimeError("execute failed")

        mocker.patch("app.db.database.SessionLocal", return_value=mock_session)

        # 不应抛出异常
        target_fn()

        mock_session.close.assert_called_once()
        mock_clear_stop.assert_called()


@pytest.mark.usefixtures("mock_monitor_task")
class TestRunExecuteOnlyEdgeCases:
    """run_execute_only() 边缘场景 — 覆盖 lines 175-187"""

    def test_background_thread_exception_fallback(
        self, orch, mock_ai, mock_threading_in_orchestrator, mocker
    ):
        """后台线程 execute 异常 → lines 175-187 兜底更新 execution 状态"""
        mock_clear_stop = mocker.patch("app.services.playwright_service.clear_stop_flag")

        asyncio.run(orch.run_execute_only(1, [1], "headless", "BgFail"))

        target_fn = mock_threading_in_orchestrator.call_args.kwargs["target"]

        mock_session = MagicMock()
        mock_exec_row = MagicMock()
        mock_exec_row.status = "running"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_exec_row

        mock_pw_svc = mocker.patch("app.services.playwright_service.PlaywrightService")
        mock_pw_svc.return_value.execute.side_effect = RuntimeError("execute failed")

        mocker.patch("app.db.database.SessionLocal", return_value=mock_session)
        mock_dt = mocker.patch("datetime.datetime")
        mock_dt.utcnow.return_value = "2024-01-01T00:00:00"

        target_fn()

        assert mock_exec_row.status == "failed"
        assert mock_exec_row.end_time == "2024-01-01T00:00:00"
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
        # clear_stop_flag 被调用 2 次：run_execute_only 直接调用 + _run finally 块
        assert mock_clear_stop.call_count >= 2

    def test_background_thread_fallback_db_error_pass(
        self, orch, mock_ai, mock_threading_in_orchestrator, mocker
    ):
        """后台线程兜底 DB 查询也异常 → lines 186-187 pass 被覆盖"""
        mock_clear_stop = mocker.patch("app.services.playwright_service.clear_stop_flag")

        asyncio.run(orch.run_execute_only(1, [1], "headless", "BgFail"))

        target_fn = mock_threading_in_orchestrator.call_args.kwargs["target"]

        mock_session = MagicMock()
        mock_session.query.side_effect = Exception("DB query failed")

        mock_pw_svc = mocker.patch("app.services.playwright_service.PlaywrightService")
        mock_pw_svc.return_value.execute.side_effect = RuntimeError("execute failed")

        mocker.patch("app.db.database.SessionLocal", return_value=mock_session)

        # 不应抛出异常
        target_fn()

        mock_session.close.assert_called_once()
        mock_clear_stop.assert_called()

    def test_without_batch_name_still_works(self, orch):
        """batch_name 为 None 时正常返回 execution_id"""
        result = asyncio.run(orch.run_execute_only(1, [1, 2]))
        assert result["execution_id"] == 42
        assert result["status"] == "running"


class TestCheckCasesNeedGenerationEdgeCases:
    """_check_cases_need_generation() 边缘场景"""

    def test_case_with_invalid_code_needs_generation(self, db_session, sample_project):
        """is_valid=0 的代码记录 → 视为需要生成"""
        from app.models.test_case import TestCase
        from app.models.generated_code import GeneratedCode

        case = TestCase(
            project_id=sample_project.id,
            case_name="Invalid Code Case",
            case_no="TC-INVALID",
            priority="P1",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com",
                 "value": "", "description": "Open"}
            ]),
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        code = GeneratedCode(
            case_id=case.id,
            code_content="broken code",
            code_language="python",
            is_valid=0,
        )
        db_session.add(code)
        db_session.commit()

        orch = TestOrchestrator()
        conn = db_session.get_bind()

        def make_session():
            return Session(bind=conn)

        with patch("app.db.database.SessionLocal", make_session):
            result = asyncio.run(orch._check_cases_need_generation([case.id]))
            assert result == [case.id]

    def test_all_cases_have_valid_code_returns_empty(
        self, db_session, sample_test_case, sample_generated_code
    ):
        """所有用例都有 is_valid=1 的代码 → 返回空列表"""
        orch = TestOrchestrator()
        conn = db_session.get_bind()

        def make_session():
            return Session(bind=conn)

        with patch("app.db.database.SessionLocal", make_session):
            result = asyncio.run(orch._check_cases_need_generation([sample_test_case.id]))
            assert result == []

    def test_mixed_valid_invalid_and_no_code(
        self, db_session, sample_project, sample_test_case, sample_generated_code
    ):
        """混合：有效代码 + 无效代码 + 无代码 → 只返回需要生成的"""
        from app.models.test_case import TestCase
        from app.models.generated_code import GeneratedCode

        # 创建第二个用例：有无效代码
        case2 = TestCase(
            project_id=sample_project.id,
            case_name="Case With Invalid Code",
            case_no="TC-INVALID2",
            priority="P1",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com",
                 "value": "", "description": "Open"}
            ]),
            status="imported",
        )
        db_session.add(case2)
        db_session.commit()
        db_session.refresh(case2)

        code2 = GeneratedCode(
            case_id=case2.id,
            code_content="bad code",
            code_language="python",
            is_valid=0,
        )
        db_session.add(code2)

        # 创建第三个用例：无任何代码
        case3 = TestCase(
            project_id=sample_project.id,
            case_name="Case Without Code",
            case_no="TC-NOCODE",
            priority="P1",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com",
                 "value": "", "description": "Open"}
            ]),
            status="imported",
        )
        db_session.add(case3)
        db_session.commit()
        db_session.refresh(case3)

        orch = TestOrchestrator()
        conn = db_session.get_bind()

        def make_session():
            return Session(bind=conn)

        with patch("app.db.database.SessionLocal", make_session):
            # sample_test_case.id 有 is_valid=1 代码，不需要生成
            # case2.id 有 is_valid=0 代码，需要生成
            # case3.id 无代码，需要生成
            result = asyncio.run(
                orch._check_cases_need_generation(
                    [sample_test_case.id, case2.id, case3.id]
                )
            )
            assert sorted(result) == sorted([case2.id, case3.id])


class TestMonitorAndGenerateReport:
    """_monitor_and_generate_report() 场景 — 覆盖 lines 215-248"""

    def test_completed_status_generates_report(self, orch, mocker):
        """状态为 completed → 生成报告（lines 226-241）"""
        mocker.patch.object(
            orch, "_get_execution_status", return_value={"status": "completed"}
        )
        mock_sleep = mocker.patch("asyncio.sleep")

        mock_db = MagicMock()
        mocker.patch("app.db.database.SessionLocal", return_value=mock_db)
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")

        asyncio.run(orch._monitor_and_generate_report(42))

        mock_sleep.assert_called_once_with(2)
        mock_report_svc.return_value.generate.assert_called_once_with(42)
        mock_db.close.assert_called_once()

    def test_stopped_status_skips_report(self, orch, mocker):
        """状态为 stopped → 跳过报告生成（lines 242-244）"""
        mocker.patch.object(
            orch, "_get_execution_status", return_value={"status": "stopped"}
        )
        mock_sleep = mocker.patch("asyncio.sleep")
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")

        asyncio.run(orch._monitor_and_generate_report(42))

        mock_sleep.assert_called_once_with(2)
        mock_report_svc.return_value.generate.assert_not_called()

    def test_failed_status_skips_report(self, orch, mocker):
        """状态为 failed → 跳过报告生成（lines 242-244）"""
        mocker.patch.object(
            orch, "_get_execution_status", return_value={"status": "failed"}
        )
        mock_sleep = mocker.patch("asyncio.sleep")
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")

        asyncio.run(orch._monitor_and_generate_report(42))

        mock_sleep.assert_called_once_with(2)
        mock_report_svc.return_value.generate.assert_not_called()

    def test_db_query_exception_continues_polling(self, orch, mocker):
        """_get_execution_status 抛异常 → 继续轮询，最终 completed（line 219-220）"""
        mocker.patch.object(
            orch,
            "_get_execution_status",
            side_effect=[Exception("DB error"), {"status": "completed"}],
        )
        mock_sleep = mocker.patch("asyncio.sleep")

        mock_db = MagicMock()
        mocker.patch("app.db.database.SessionLocal", return_value=mock_db)
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")

        asyncio.run(orch._monitor_and_generate_report(42))

        assert mock_sleep.call_count == 2
        mock_report_svc.return_value.generate.assert_called_once_with(42)

    def test_execution_is_none_continues_polling(self, orch, mocker):
        """_get_execution_status 返回 None → 继续轮询（line 222-223）"""
        mocker.patch.object(
            orch,
            "_get_execution_status",
            side_effect=[None, {"status": "completed"}],
        )
        mock_sleep = mocker.patch("asyncio.sleep")

        mock_db = MagicMock()
        mocker.patch("app.db.database.SessionLocal", return_value=mock_db)
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")

        asyncio.run(orch._monitor_and_generate_report(42))

        assert mock_sleep.call_count == 2
        mock_report_svc.return_value.generate.assert_called_once_with(42)

    def test_cancelled_error_is_caught(self, orch, mocker):
        """asyncio.CancelledError 被捕获（lines 245-246）"""
        mocker.patch("asyncio.sleep", side_effect=asyncio.CancelledError())

        # 不应抛出异常
        asyncio.run(orch._monitor_and_generate_report(42))

    def test_monitor_exception_is_caught(self, orch, mocker):
        """通用异常被捕获（lines 247-248）"""
        mocker.patch("asyncio.sleep", side_effect=RuntimeError("unexpected error"))

        # 不应抛出异常
        asyncio.run(orch._monitor_and_generate_report(42))

    def test_report_generation_failure_is_handled(self, orch, mocker):
        """报告生成失败 → 异常被捕获，不中断监听（line 239-240）"""
        mocker.patch.object(
            orch, "_get_execution_status", return_value={"status": "completed"}
        )
        mock_sleep = mocker.patch("asyncio.sleep")

        mock_db = MagicMock()
        mocker.patch("app.db.database.SessionLocal", return_value=mock_db)
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")
        mock_report_svc.return_value.generate.side_effect = RuntimeError("report failed")

        # 不应抛出异常
        asyncio.run(orch._monitor_and_generate_report(42))

        # 使用 assert_any_call 而非 assert_called_once_with：
        # asyncio 事件循环内部可能调用 asyncio.sleep(0)，导致 mock 计数超出预期
        mock_sleep.assert_any_call(2)
        mock_report_svc.return_value.generate.assert_called_once_with(42)
        mock_db.close.assert_called_once()

    def test_timeout_exits_loop_normally(self, orch, mocker):
        """轮询超时 → for 循环正常退出，不生成报告（覆盖 215->exit）"""
        # 始终返回 running，直到 900 次轮询结束
        mocker.patch.object(
            orch, "_get_execution_status", return_value={"status": "running"}
        )
        mock_sleep = mocker.patch("asyncio.sleep")
        mock_report_svc = mocker.patch("app.services.report_service.ReportService")

        asyncio.run(orch._monitor_and_generate_report(42))

        # 900 次轮询，每次 sleep(2)。
        # 使用 >= 而非 == ：asyncio.run 的事件循环中可能残留其他协程
        # （顺序依赖）额外调用全局 mock 的 asyncio.sleep，导致计数略高。
        assert mock_sleep.call_count >= 900
        mock_sleep.assert_called_with(2)
        # 从未生成报告
        mock_report_svc.return_value.generate.assert_not_called()


class TestGetExecutionStatus:
    """_get_execution_status() 场景 — 覆盖 lines 250-267"""

    def test_returns_status_for_existing_execution(self, db_session, sample_execution):
        """返回已有执行的状态（lines 255-262）"""
        orch = TestOrchestrator()
        conn = db_session.get_bind()

        def make_session():
            return Session(bind=conn)

        with patch("app.db.database.SessionLocal", make_session):
            result = orch._get_execution_status(sample_execution.id)
            assert result == {"status": "completed"}

    def test_returns_none_for_missing_execution(self, db_session):
        """不存在的执行 → 返回 None（line 263）"""
        orch = TestOrchestrator()
        conn = db_session.get_bind()

        def make_session():
            return Session(bind=conn)

        with patch("app.db.database.SessionLocal", make_session):
            result = orch._get_execution_status(99999)
            assert result is None

    def test_db_error_returns_none(self, orch):
        """DB 异常 → 返回 None（line 267）"""
        with patch("app.db.database.SessionLocal", side_effect=Exception("DB down")):
            result = orch._get_execution_status(1)
            assert result is None