"""pipeline_service：触发（走 Admission）/ 防重入 / 派生汇总 — RED 先行

Spec §5 / §6 / §11：
  - AC-02：触发只构造执行请求，execution 由 ExecutionAdmission 创建（禁第二 Contract）；
  - AC-03：同一 pipeline 已有 running run → 409，不产生第二 run；
  - AC-04：run.status 由下属 executions 终态派生（非独立状态机）。
"""

import json
from datetime import datetime, timedelta

import pytest

from app.exceptions import AppException
from app.models.execution import Execution
from app.models.pipeline import Pipeline, PipelineRun
from app.services.pipeline_service import PipelineService

_NOW = datetime(2026, 10, 7, 10, 0, 0)


def _mk_pipeline(db, project_id, *, case_ids=(1,), enabled=True, name="ci") -> Pipeline:
    p = Pipeline(
        project_id=project_id,
        name=name,
        trigger_config_json=json.dumps({"manual": True}),
        stages_json=json.dumps([
            {"name": "smoke", "case_selector": {"case_ids": list(case_ids)}, "env": "staging"},
        ]),
        enabled=enabled,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _add_run(db, pipeline_id, status="running") -> PipelineRun:
    run = PipelineRun(
        pipeline_id=pipeline_id,
        trigger_type="manual",
        status=status,
        started_at=_NOW,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _add_execution(db, project_id, run_id, status) -> Execution:
    ex = Execution(
        project_id=project_id,
        batch_name="pipeline:ci#1:smoke",
        total_cases=1,
        status=status,
        start_time=_NOW,
        pipeline_run_id=run_id,
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


class TestTriggerThroughAdmission:
    """AC-02：真实 orchestrator + 真实 Admission，仅短路环境检查与后台线程"""

    async def test_trigger_goes_through_admission(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mocker, mock_threading_in_orchestrator, mock_monitor_task,
    ):
        from app.dependencies import get_orchestrator
        from app.services.execution_admission_service import ExecutionAdmissionService

        mocker.patch(
            "app.services.orchestrator.TestOrchestrator._pre_execution_check",
            new=mocker.AsyncMock(return_value=None),
        )
        admit_spy = mocker.spy(ExecutionAdmissionService, "admit")

        pipeline = _mk_pipeline(db_session, sample_project.id,
                                case_ids=[sample_test_case.id])
        orch = get_orchestrator(db_session)
        svc = PipelineService(db=db_session, orchestrator=orch)

        result = await svc.trigger(pipeline.id, trigger_type="manual")

        assert admit_spy.call_count == 1, "触发必须经过 ExecutionAdmission 唯一入口"
        args = admit_spy.call_args.args  # (self, project_id, case_ids, ...)
        assert args[1] == sample_project.id
        assert list(args[2]) == [sample_test_case.id]

        # execution 由 Admission 创建，事后仅被标注 pipeline_run_id
        marked = (
            db_session.query(Execution)
            .filter(Execution.pipeline_run_id == result["run_id"])
            .all()
        )
        assert len(marked) == 1
        assert marked[0].project_id == sample_project.id


class TestReentryGuard:
    """AC-03：running 中再次触发 → 409，不产生第二 run"""

    async def test_reentry_rejected_409(self, db_session, sample_project, mocker):
        pipeline = _mk_pipeline(db_session, sample_project.id)
        _add_run(db_session, pipeline.id, status="running")
        orch = mocker.AsyncMock()
        svc = PipelineService(db=db_session, orchestrator=orch)

        with pytest.raises(AppException) as ei:
            await svc.trigger(pipeline.id, trigger_type="manual")

        assert ei.value.status_code == 409
        orch.run_execute_only.assert_not_awaited()
        assert db_session.query(PipelineRun).filter(
            PipelineRun.pipeline_id == pipeline.id).count() == 1

    async def test_disabled_pipeline_rejected_409(self, db_session, sample_project, mocker):
        pipeline = _mk_pipeline(db_session, sample_project.id, enabled=False)
        orch = mocker.AsyncMock()
        svc = PipelineService(db=db_session, orchestrator=orch)

        with pytest.raises(AppException) as ei:
            await svc.trigger(pipeline.id, trigger_type="manual")

        assert ei.value.status_code == 409
        assert db_session.query(PipelineRun).filter(
            PipelineRun.pipeline_id == pipeline.id).count() == 0


class TestDerivedStatus:
    """AC-04：run.status 派生自下属 executions 终态（无独立状态机）"""

    @pytest.mark.parametrize("statuses,expected", [
        (["completed", "completed"], "success"),
        (["completed", "failed"], "failed"),
        (["completed", "stopped"], "failed"),
        (["completed", "interrupted"], "failed"),
        (["running", "completed"], "running"),
        (["queued", "queued"], "queued"),
    ])
    def test_derive_status_matrix(self, db_session, sample_project, statuses, expected):
        pipeline = _mk_pipeline(db_session, sample_project.id)
        run = _add_run(db_session, pipeline.id, status="queued")
        for st in statuses:
            _add_execution(db_session, sample_project.id, run.id, st)

        svc = PipelineService(db=db_session)
        assert svc.derive_status(run.id) == expected

    def test_derive_status_no_execution_is_queued(self, db_session, sample_project):
        pipeline = _mk_pipeline(db_session, sample_project.id)
        run = _add_run(db_session, pipeline.id, status="queued")
        svc = PipelineService(db=db_session)
        assert svc.derive_status(run.id) == "queued"


class TestSourceNoSecondContract:
    """AC-02 Source 证据：触发只调 orchestrator 正式入口"""

    def test_source_references_orchestrator_entry(self):
        import inspect
        from app.services import pipeline_service as mod

        src = inspect.getsource(mod)
        assert "run_execute_only" in src
        assert "execution_state" not in src
