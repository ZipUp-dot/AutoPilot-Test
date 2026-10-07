"""pipelines 管理 API（CRUD + trigger + runs 视图）— RED 先行

Spec §6 / §7 / §11：
  - CRUD：创建 / 列表 / 详情 / 更新 / 删除；
  - trigger：手动触发（本仓无用户体系，manual 不需 token）；
  - runs 列表 + run 详情（逐阶段 execution 状态/通过率，**与 executions 接口同口径**，AC-05）。
"""

import json
from datetime import datetime

import pytest

_NOW = datetime(2026, 10, 7, 10, 0, 0)


def _body(**overrides):
    body = {
        "name": "ci",
        "trigger_config_json": {"manual": True},
        "stages_json": [
            {"name": "smoke", "case_selector": {"case_ids": [1]}, "env": "staging"},
        ],
    }
    body.update(overrides)
    return body


def _create(client, project_id, **overrides):
    return client.post(f"/api/v1/projects/{project_id}/pipelines", json=_body(**overrides))


class TestPipelineCrud:
    def test_create_returns_fields(self, client, sample_project):
        r = _create(client, sample_project.id)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["project_id"] == sample_project.id
        assert d["name"] == "ci"
        assert d["enabled"] is True
        assert d["stages_json"][0]["name"] == "smoke"
        assert d["trigger_config_json"]["manual"] is True

    def test_create_unknown_project_404(self, client):
        assert _create(client, 999999).status_code == 404

    def test_create_empty_stages_422(self, client, sample_project):
        assert _create(client, sample_project.id, stages_json=[]).status_code == 422

    def test_create_missing_name_422(self, client, sample_project):
        r = client.post(f"/api/v1/projects/{sample_project.id}/pipelines",
                        json={"trigger_config_json": {}, "stages_json": _body()["stages_json"]})
        assert r.status_code == 422

    def test_list_and_detail(self, client, sample_project):
        pid = _create(client, sample_project.id).json()["data"]["id"]
        lst = client.get(f"/api/v1/projects/{sample_project.id}/pipelines").json()["data"]
        assert lst["total"] == 1
        det = client.get(f"/api/v1/pipelines/{pid}")
        assert det.status_code == 200
        assert det.json()["data"]["id"] == pid

    def test_update(self, client, sample_project):
        pid = _create(client, sample_project.id).json()["data"]["id"]
        r = client.put(f"/api/v1/pipelines/{pid}", json={"name": "ci-2", "enabled": False})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["name"] == "ci-2"
        assert d["enabled"] is False

    def test_delete(self, client, sample_project):
        pid = _create(client, sample_project.id).json()["data"]["id"]
        assert client.delete(f"/api/v1/pipelines/{pid}").status_code == 200
        assert client.get(f"/api/v1/pipelines/{pid}").status_code == 404


class TestTriggerAndRuns:
    def test_manual_trigger_creates_run(self, client, sample_project, mocker):
        pid = _create(client, sample_project.id).json()["data"]["id"]
        orch = mocker.AsyncMock()
        orch.run_execute_only.return_value = {"execution_id": 42, "status": "running"}
        mocker.patch("app.routers.pipelines.get_orchestrator", return_value=orch)

        r = client.post(f"/api/v1/pipelines/{pid}/trigger", json={"trigger_type": "manual"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["run_id"]
        assert d["trigger_type"] == "manual"
        orch.run_execute_only.assert_awaited_once()

        runs = client.get(f"/api/v1/pipelines/{pid}/runs").json()["data"]
        assert runs["total"] == 1
        assert runs["items"][0]["id"] == d["run_id"]

    def test_run_detail_stages(self, client, db_session, sample_project, mocker):
        from app.models.execution import Execution

        pid = _create(client, sample_project.id).json()["data"]["id"]

        async def _fake_run_execute_only(project_id, case_ids, mode, batch_name, platform):
            # 替身仅模拟 orchestrator 的外部行为：execution 事实仍由服务层标注归属
            ex = Execution(project_id=project_id, batch_name=batch_name,
                           total_cases=len(case_ids), status="queued")
            db_session.add(ex)
            db_session.commit()
            db_session.refresh(ex)
            return {"execution_id": ex.id, "status": "queued"}

        orch = mocker.AsyncMock()
        orch.run_execute_only.side_effect = _fake_run_execute_only
        mocker.patch("app.routers.pipelines.get_orchestrator", return_value=orch)

        trig = client.post(f"/api/v1/pipelines/{pid}/trigger",
                           json={"trigger_type": "manual"}).json()["data"]
        assert trig["stages"][0]["execution_id"] is not None

        det = client.get(f"/api/v1/pipeline-runs/{trig['run_id']}")
        assert det.status_code == 200, det.text
        d = det.json()["data"]
        assert d["pipeline_id"] == pid
        assert d["stages"][0]["name"] == "smoke"
        assert d["stages"][0]["execution_id"] == trig["stages"][0]["execution_id"]

    def test_run_detail_unknown_404(self, client, sample_project):
        assert client.get("/api/v1/pipeline-runs/999999").status_code == 404

    def test_trigger_unknown_404(self, client, sample_project):
        assert client.post("/api/v1/pipelines/999999/trigger",
                           json={"trigger_type": "manual"}).status_code == 404


class TestRunStatsSameCaliberAsExecutions:
    """AC-05：run 视图逐 execution 统计与 /executions 接口同口径（CaseStateResolver）"""

    def test_stage_stats_match_execution_api(self, client, db_session, sample_project,
                                             sample_test_case, sample_execution):
        # 造一条带 pipeline_run_id 的执行（样本执行：completed + 1 个 success step）
        from app.models.pipeline import Pipeline, PipelineRun
        from app.models.execution import Execution

        pipeline = Pipeline(
            project_id=sample_project.id,
            name="ci",
            trigger_config_json=json.dumps({"manual": True}),
            stages_json=json.dumps([
                {"name": "smoke", "case_selector": {"case_ids": [sample_test_case.id]}, "env": None},
            ]),
            enabled=True,
        )
        db_session.add(pipeline)
        db_session.commit()
        db_session.refresh(pipeline)

        run = PipelineRun(pipeline_id=pipeline.id, trigger_type="manual",
                          status="success", started_at=_NOW)
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        ex = db_session.query(Execution).filter(Execution.id == sample_execution.id).first()
        ex.pipeline_run_id = run.id
        ex.batch_name = f"pipeline:{pipeline.name}#{run.id}:smoke"
        db_session.commit()

        exec_api = client.get(f"/api/v1/executions/{ex.id}").json()["data"]
        run_api = client.get(f"/api/v1/pipeline-runs/{run.id}").json()["data"]

        stage = run_api["stages"][0]
        assert stage["execution_id"] == ex.id
        assert stage["passed_cases"] == exec_api["passed_cases"]
        assert stage["failed_cases"] == exec_api["failed_cases"]
        assert stage["total_cases"] == exec_api["total_cases"]
