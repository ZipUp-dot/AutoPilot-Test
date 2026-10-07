"""定时触发 → Admission 集成链（AC-02 / AC-03 / AC-04）— RED 先行

Spec §5 / §8 / §11：
  - AC-02：到点触发必须走既有 ExecutionAdmission（唯一入口），链路
    SchedulerService → orchestrator.run_execute_only → ExecutionAdmissionService.admit；
  - AC-03：disable 后不再触发，且运行中 Execution 不被中断；
  - AC-04：手动触发与定时触发同权（同一 orchestrator 入口）。
"""

import json
from datetime import datetime, timedelta

from app.models.execution import Execution
from app.models.schedule import Schedule
from app.services.execution_admission_service import ExecutionAdmissionService
from app.services.scheduler_service import SchedulerService


def _add_schedule(db, project_id, case_ids, *, enabled=True, next_run_at=None):
    sched = Schedule(
        project_id=project_id,
        name="integration",
        cron_expr="*/5 * * * *",
        exec_config_json=json.dumps({"case_ids": list(case_ids), "mode": "headless"}),
        enabled=enabled,
        next_run_at=next_run_at,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return sched


class TestSchedulerAdmissionChain:
    """AC-02：真实 orchestrator + 真实 Admission，仅短路环境检查与后台线程"""

    async def test_due_trigger_goes_through_admission(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mocker, mock_threading_in_orchestrator, mock_monitor_task,
    ):
        from app.dependencies import get_orchestrator

        # 短路：执行前环境健康检查（真实实现会访问目标站点）
        mocker.patch(
            "app.services.orchestrator.TestOrchestrator._pre_execution_check",
            new=mocker.AsyncMock(return_value=None),
        )
        # 探针：Admission 必须被调用（进入唯一入口）
        admit_spy = mocker.spy(ExecutionAdmissionService, "admit")

        now = datetime(2026, 10, 7, 10, 0, 0)
        sched = _add_schedule(db_session, sample_project.id, [sample_test_case.id],
                              next_run_at=now - timedelta(seconds=1))

        orch = get_orchestrator(db_session)
        svc = SchedulerService(db=db_session, orchestrator=orch)

        results = await svc.run_due(now=now)

        assert admit_spy.call_count == 1, "触发必须经过 ExecutionAdmission 唯一入口"
        # spy 记录未绑定调用：(self, project_id, case_ids, ...)
        args = admit_spy.call_args.args
        assert args[1] == sample_project.id
        assert list(args[2]) == [sample_test_case.id]

        db_session.refresh(sched)
        assert sched.last_execution_id is not None
        created = db_session.query(Execution).filter(Execution.id == sched.last_execution_id).first()
        assert created is not None
        assert created.project_id == sample_project.id
        assert results and results[0]["status"] == "triggered"


class TestDisableSemantics:
    """AC-03：disable 后不再触发 + 运行中 Execution 不中断"""

    async def test_disabled_schedule_not_triggered_after_disable(
        self, db_session, sample_project, sample_test_case, mocker,
    ):
        now = datetime(2026, 10, 7, 10, 0, 0)
        sched = _add_schedule(db_session, sample_project.id, [sample_test_case.id],
                              enabled=True, next_run_at=now - timedelta(seconds=1))
        sched.enabled = False  # 等价于"停止调度"写入后的状态
        sched.stop_requested_at = now
        db_session.commit()

        orch = mocker.AsyncMock()
        svc = SchedulerService(db=db_session, orchestrator=orch)
        results = await svc.run_due(now=now)

        assert results == []
        orch.run_execute_only.assert_not_awaited()

    async def test_disable_keeps_running_execution_untouched(
        self, db_session, sample_project, sample_test_case, mocker,
    ):
        running = Execution(
            project_id=sample_project.id,
            batch_name="live-batch",
            total_cases=1,
            status="running",
            start_time=datetime.utcnow(),
        )
        db_session.add(running)
        db_session.commit()
        db_session.refresh(running)

        now = datetime(2026, 10, 7, 10, 0, 0)
        sched = _add_schedule(db_session, sample_project.id, [sample_test_case.id],
                              enabled=True, next_run_at=now + timedelta(hours=1))
        sched.enabled = False
        sched.stop_requested_at = now
        db_session.commit()

        db_session.refresh(running)
        assert running.status == "running"
        assert running.stop_requested_at is None


class TestManualScheduledParity:
    """AC-04：手动触发与定时触发同权（同一入口）"""

    async def test_manual_and_scheduled_share_entry(
        self, db_session, sample_project, sample_test_case, mocker,
    ):
        now = datetime(2026, 10, 7, 10, 0, 0)
        manual = _add_schedule(db_session, sample_project.id, [sample_test_case.id],
                               next_run_at=now + timedelta(hours=1))
        scheduled = _add_schedule(db_session, sample_project.id, [sample_test_case.id],
                                  next_run_at=now - timedelta(seconds=1))

        orch = mocker.AsyncMock()
        orch.run_execute_only.return_value = {"execution_id": 1, "status": "running"}
        svc = SchedulerService(db=db_session, orchestrator=orch)

        await svc.run_due(now=now)
        await svc.trigger_now(manual.id, now=now)

        assert orch.run_execute_only.await_count == 2
        calls = orch.run_execute_only.call_args_list
        for c in calls:
            assert c.kwargs["project_id"] == sample_project.id
            assert c.kwargs["case_ids"] == [sample_test_case.id]
