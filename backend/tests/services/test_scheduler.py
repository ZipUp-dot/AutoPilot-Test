"""调度触发逻辑（AC-02 / AC-03 单元面，mock 时钟）— RED 先行

Spec §5 / §6 / §11：
  - 到点（next_run_at <= now 且 enabled）→ 触发，且触发只调 orchestrator 正式入口
    （run_execute_only），禁止直调 execution_state / admission；
  - 触发后回填 last_run_at / last_execution_id / next_run_at；
  - disable（enabled=False）后不再触发；
  - next_run_at 未到 → 不触发。
"""

import json
from datetime import datetime, timedelta

import pytest

from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService


def _mk_schedule(
    db,
    project_id: int,
    *,
    cron: str = "*/5 * * * *",
    enabled: bool = True,
    next_run_at: datetime | None = None,
    case_ids=(1,),
    mode: str = "headless",
) -> Schedule:
    sched = Schedule(
        project_id=project_id,
        name="nightly",
        cron_expr=cron,
        exec_config_json=json.dumps({"case_ids": list(case_ids), "mode": mode}),
        enabled=enabled,
        next_run_at=next_run_at,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return sched


class TestRunDue:
    """到点判定 + 触发 + 回填（mock 时钟）"""

    async def test_due_schedule_triggers_via_orchestrator(self, db_session, sample_project, mocker):
        now = datetime(2026, 10, 7, 10, 0, 0)
        sched = _mk_schedule(db_session, sample_project.id,
                             next_run_at=now - timedelta(seconds=1))
        orch = mocker.AsyncMock()
        orch.run_execute_only.return_value = {"execution_id": 77, "status": "running"}

        svc = SchedulerService(db=db_session, orchestrator=orch)
        results = await svc.run_due(now=now)

        assert len(results) == 1
        orch.run_execute_only.assert_awaited_once()
        kwargs = orch.run_execute_only.call_args.kwargs
        assert kwargs["project_id"] == sample_project.id
        assert kwargs["case_ids"] == [1]
        assert kwargs["mode"] == "headless"
        assert kwargs["platform"] == "web"

        db_session.refresh(sched)
        assert sched.last_execution_id == 77
        assert sched.last_run_at == now
        assert sched.next_run_at > now

    async def test_disabled_schedule_not_triggered(self, db_session, sample_project, mocker):
        now = datetime(2026, 10, 7, 10, 0, 0)
        _mk_schedule(db_session, sample_project.id, enabled=False,
                     next_run_at=now - timedelta(minutes=1))
        orch = mocker.AsyncMock()

        svc = SchedulerService(db=db_session, orchestrator=orch)
        results = await svc.run_due(now=now)

        assert results == []
        orch.run_execute_only.assert_not_awaited()

    async def test_future_schedule_not_triggered(self, db_session, sample_project, mocker):
        now = datetime(2026, 10, 7, 10, 0, 0)
        _mk_schedule(db_session, sample_project.id,
                     next_run_at=now + timedelta(minutes=5))
        orch = mocker.AsyncMock()

        svc = SchedulerService(db=db_session, orchestrator=orch)
        results = await svc.run_due(now=now)

        assert results == []
        orch.run_execute_only.assert_not_awaited()

    async def test_trigger_failure_isolated(self, db_session, sample_project, mocker):
        """单条触发异常不影响其它条（异常隔离）"""
        now = datetime(2026, 10, 7, 10, 0, 0)
        _mk_schedule(db_session, sample_project.id, next_run_at=now - timedelta(seconds=1))
        orch = mocker.AsyncMock()
        orch.run_execute_only.side_effect = RuntimeError("boom")

        svc = SchedulerService(db=db_session, orchestrator=orch)
        results = await svc.run_due(now=now)

        # 失败条目被记录（status=failed），不向上抛
        assert len(results) == 1
        assert results[0]["status"] == "failed"


class TestManualTriggerSameEntry:
    """AC-04：手动触发与定时触发同权（同一 orchestrator 入口）"""

    async def test_trigger_now_uses_same_orchestrator_entry(self, db_session, sample_project, mocker):
        now = datetime(2026, 10, 7, 10, 0, 0)
        sched = _mk_schedule(db_session, sample_project.id,
                             next_run_at=now + timedelta(hours=1))
        orch = mocker.AsyncMock()
        orch.run_execute_only.return_value = {"execution_id": 99, "status": "running"}

        svc = SchedulerService(db=db_session, orchestrator=orch)
        result = await svc.trigger_now(sched.id, now=now)

        assert result["execution_id"] == 99
        orch.run_execute_only.assert_awaited_once()
        kwargs = orch.run_execute_only.call_args.kwargs
        assert kwargs["project_id"] == sample_project.id
        assert kwargs["case_ids"] == [1]

        db_session.refresh(sched)
        assert sched.last_execution_id == 99
        # 手动触发不改动调度节奏
        assert sched.next_run_at == now + timedelta(hours=1)


class TestSourceNoBypass:
    """AC-02 Source 证据：触发只走 orchestrator 正式入口"""

    def test_source_references_orchestrator_entry(self):
        import inspect
        from app.services import scheduler_service as mod

        src = inspect.getsource(mod)
        assert "run_execute_only" in src, "必须调用 orchestrator.run_execute_only"

    def test_source_has_no_direct_execution_bypass(self):
        import inspect
        from app.services import scheduler_service as mod

        src = inspect.getsource(mod)
        # 禁止绕过 Admission 的旁路：不得直接引用 admission / execution_state
        assert "execution_admission_service" not in src
        assert "execution_state" not in src
