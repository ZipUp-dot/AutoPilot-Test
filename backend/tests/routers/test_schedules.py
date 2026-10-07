"""定时任务管理 API（CRUD + enable/disable + 手动触发）— RED 先行

Spec §6 / §11：
  - CRUD：创建 / 列表 / 详情 / 更新 / 删除；
  - enable / disable：disable 即"停止调度"（enabled=False + 写 stop_requested_at），
    enable 仅置 enabled=True（stop_requested_at 是历史留痕，不被 enable 覆写）；
  - 手动触发一次：与定时触发同一 orchestrator 入口（AC-04）。
"""

import json

import pytest


def _create(client, project_id, **overrides):
    payload = {
        "name": "nightly",
        "cron_expr": "0 9 * * 1-5",
        "exec_config_json": {"case_ids": [1], "mode": "headless"},
    }
    payload.update(overrides)
    return client.post(f"/api/v1/projects/{project_id}/schedules", json=payload)


class TestScheduleCrud:
    def test_create_returns_fields_and_next_run(self, client, sample_project):
        resp = _create(client, sample_project.id)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["project_id"] == sample_project.id
        assert data["name"] == "nightly"
        assert data["cron_expr"] == "0 9 * * 1-5"
        assert data["enabled"] is True
        assert data["next_run_at"] is not None
        assert data["last_run_at"] is None
        assert data["last_execution_id"] is None
        assert data["stop_requested_at"] is None
        assert data["exec_config_json"]["case_ids"] == [1]

    def test_create_unknown_project_404(self, client):
        resp = _create(client, 999999)
        assert resp.status_code == 404, resp.text

    def test_list_by_project(self, client, sample_project):
        _create(client, sample_project.id, name="a")
        _create(client, sample_project.id, name="b")
        resp = client.get(f"/api/v1/projects/{sample_project.id}/schedules")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 2
        assert {i["name"] for i in data["items"]} == {"a", "b"}

    def test_get_detail(self, client, sample_project):
        sid = _create(client, sample_project.id).json()["data"]["id"]
        resp = client.get(f"/api/v1/schedules/{sid}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["id"] == sid

    def test_update_cron_recomputes_next_run(self, client, sample_project):
        sid = _create(client, sample_project.id).json()["data"]["id"]
        before = client.get(f"/api/v1/schedules/{sid}").json()["data"]["next_run_at"]
        resp = client.put(f"/api/v1/schedules/{sid}", json={"cron_expr": "*/5 * * * *"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["cron_expr"] == "*/5 * * * *"
        assert data["next_run_at"] != before

    def test_update_bad_cron_422(self, client, sample_project):
        sid = _create(client, sample_project.id).json()["data"]["id"]
        resp = client.put(f"/api/v1/schedules/{sid}", json={"cron_expr": "bad cron"})
        assert resp.status_code == 422, resp.text

    def test_delete(self, client, sample_project):
        sid = _create(client, sample_project.id).json()["data"]["id"]
        resp = client.delete(f"/api/v1/schedules/{sid}")
        assert resp.status_code == 200, resp.text
        assert client.get(f"/api/v1/schedules/{sid}").status_code == 404


class TestEnableDisable:
    def test_disable_sets_flag_and_stop_requested_at(self, client, sample_project):
        sid = _create(client, sample_project.id).json()["data"]["id"]
        resp = client.post(f"/api/v1/schedules/{sid}/disable")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["enabled"] is False
        assert data["stop_requested_at"] is not None

    def test_enable_only_toggles_flag(self, client, sample_project):
        sid = _create(client, sample_project.id).json()["data"]["id"]
        client.post(f"/api/v1/schedules/{sid}/disable")
        resp = client.post(f"/api/v1/schedules/{sid}/enable")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["enabled"] is True
        assert data["next_run_at"] is not None

    def test_disable_does_not_touch_running_execution(self, client, db_session, sample_project):
        """AC-03：disable 只改 Schedule，不触碰运行中 Execution（两实体语义分离）"""
        from datetime import datetime
        from app.models.execution import Execution

        running = Execution(
            project_id=sample_project.id,
            batch_name="running-batch",
            total_cases=1,
            status="running",
            start_time=datetime.utcnow(),
        )
        db_session.add(running)
        db_session.commit()
        db_session.refresh(running)

        sid = _create(client, sample_project.id).json()["data"]["id"]
        resp = client.post(f"/api/v1/schedules/{sid}/disable")
        assert resp.status_code == 200, resp.text

        db_session.refresh(running)
        assert running.status == "running"
        assert running.stop_requested_at is None


class TestManualTrigger:
    def test_trigger_calls_orchestrator_entry(self, client, sample_project, mocker):
        sid = _create(client, sample_project.id).json()["data"]["id"]

        orch = mocker.AsyncMock()
        orch.run_execute_only.return_value = {"execution_id": 123, "status": "running"}
        mocker.patch("app.routers.schedules.get_orchestrator", return_value=orch)

        resp = client.post(f"/api/v1/schedules/{sid}/trigger")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["execution_id"] == 123
        orch.run_execute_only.assert_awaited_once()

    def test_trigger_unknown_404(self, client, sample_project):
        resp = client.post("/api/v1/schedules/999999/trigger")
        assert resp.status_code == 404, resp.text
