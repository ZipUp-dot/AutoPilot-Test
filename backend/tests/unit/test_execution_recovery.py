"""Execution 状态持久化与重启恢复测试

覆盖阶段 4 验收：
  1. 创建任务 → queued → running → progress 持久化 → heartbeat 更新 → 正常完成
  2. running → 模拟服务异常 → 重启恢复 → stale running 被识别为 interrupted
"""

from datetime import datetime, timedelta

import pytest

from app.models.execution import Execution
from app.services.execution_state import recover_orphan_executions
from app.services.playwright_service import PlaywrightService


# ═══════════════════════════════════════════════
# recover_orphan_executions — 服务重启恢复
# ═══════════════════════════════════════════════

class TestRecoverOrphanExecutions:
    """启动时恢复遗留执行状态"""

    def _make_execution(self, db_session, status="queued", heartbeat=None, name="E"):
        exec_obj = Execution(
            project_id=1,
            batch_name=name,
            total_cases=2,
            status=status,
            start_time=datetime.utcnow() - timedelta(minutes=30),
            heartbeat_at=heartbeat,
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)
        return exec_obj

    def test_queued_becomes_interrupted(self, db_session):
        """queued（线程未启动即崩溃）→ interrupted"""
        e = self._make_execution(db_session, status="queued", heartbeat=None)
        n = recover_orphan_executions(db_session)
        assert n == 1
        db_session.refresh(e)
        assert e.status == "interrupted"
        assert e.end_time is not None

    def test_running_with_stale_heartbeat_becomes_interrupted(self, db_session):
        """running + 心跳超时（服务异常）→ interrupted"""
        stale = datetime.utcnow() - timedelta(seconds=3600)
        e = self._make_execution(db_session, status="running", heartbeat=stale)
        n = recover_orphan_executions(db_session)
        assert n == 1
        db_session.refresh(e)
        assert e.status == "interrupted"

    def test_running_without_heartbeat_becomes_interrupted(self, db_session):
        """running + 无心跳（旧数据/启动即崩溃）→ interrupted"""
        e = self._make_execution(db_session, status="running", heartbeat=None)
        recover_orphan_executions(db_session)
        db_session.refresh(e)
        assert e.status == "interrupted"

    def test_running_with_fresh_heartbeat_kept(self, db_session):
        """running + 心跳新鲜 → 不被误标（避免粗暴所有 running → failed）"""
        fresh = datetime.utcnow()
        e = self._make_execution(db_session, status="running", heartbeat=fresh)
        n = recover_orphan_executions(db_session)
        assert n == 0
        db_session.refresh(e)
        assert e.status == "running"

    def test_healing_with_stale_heartbeat_becomes_interrupted(self, db_session):
        """healing + 心跳超时 → interrupted"""
        stale = datetime.utcnow() - timedelta(seconds=3600)
        e = self._make_execution(db_session, status="healing", heartbeat=stale)
        recover_orphan_executions(db_session)
        db_session.refresh(e)
        assert e.status == "interrupted"

    def test_terminal_states_untouched(self, db_session):
        """已完成/停止/失败的记录不受影响"""
        for status in ("completed", "stopped", "failed", "interrupted"):
            e = self._make_execution(db_session, status=status, heartbeat=datetime.utcnow(), name=status)
        n = recover_orphan_executions(db_session)
        assert n == 0
        for name in ("completed", "stopped", "failed", "interrupted"):
            row = db_session.query(Execution).filter(Execution.batch_name == name).first()
            assert row.status == name

    def test_mixed_recovery_count(self, db_session):
        """混合场景：queued + 超时 running 恢复，新鲜 running 保留"""
        self._make_execution(db_session, status="queued", name="q")
        self._make_execution(
            db_session, status="running",
            heartbeat=datetime.utcnow() - timedelta(seconds=3600), name="stale",
        )
        self._make_execution(db_session, status="running", heartbeat=datetime.utcnow(), name="fresh")
        n = recover_orphan_executions(db_session)
        assert n == 2
        assert db_session.query(Execution).filter(Execution.batch_name == "q").first().status == "interrupted"
        assert db_session.query(Execution).filter(Execution.batch_name == "stale").first().status == "interrupted"
        assert db_session.query(Execution).filter(Execution.batch_name == "fresh").first().status == "running"


# ═══════════════════════════════════════════════
# 执行期心跳 + 进度持久化（验收：progress/heartbeat 更新）
# ═══════════════════════════════════════════════

class TestExecutionHeartbeat:
    """执行过程中逐用例更新 heartbeat_at / progress"""

    @pytest.mark.asyncio
    async def test_execute_updates_progress_and_heartbeat(self, db_session, sample_project, sample_test_case, sample_generated_code, mocker):
        """执行完成后：progress=100，heartbeat_at 已更新（正常完成链路）"""
        mock_page = mocker.AsyncMock()
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
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())
        mocker.patch.object(PlaywrightService, "_execute_case", return_value=True)
        mocker.patch.object(PlaywrightService, "_start_healing")

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="queued",
            start_time=datetime.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()
        db_session.refresh(exec_obj)

        svc = PlaywrightService(db_session)
        await svc._execute_async(sample_project.id, [sample_test_case.id], exec_obj.id, "headless")

        db_session.refresh(exec_obj)
        assert exec_obj.status == "completed"
        assert exec_obj.progress == 100
        assert exec_obj.heartbeat_at is not None
        assert exec_obj.passed_cases == 1
