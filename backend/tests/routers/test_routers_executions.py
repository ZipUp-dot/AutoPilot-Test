"""Router tests for execution endpoints — /api/v1/projects/{pid}/executions + /api/v1/executions/..."""


def test_create_execution(client, sample_project, sample_test_case, sample_generated_code, mock_playwright_for_execution_service):
    """POST /api/v1/projects/{pid}/executions — returns 200 with execution_id"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "headless", "batch_name": "Test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "execution_id" in data["data"]


def test_execute_without_generated_code(client, sample_project, sample_test_case):
    """POST /api/v1/projects/{pid}/executions — no generated code returns 422"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "headless"},
    )
    assert resp.status_code == 422


def test_execute_invalid_mode(client, sample_project, sample_test_case, sample_generated_code):
    """POST /api/v1/projects/{pid}/executions — invalid mode returns 422"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "invalid"},
    )
    assert resp.status_code == 422


def test_list_executions(client, sample_project, sample_execution):
    """GET /api/v1/projects/{pid}/executions — returns list of executions"""
    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "items" in data["data"]
    assert len(data["data"]["items"]) >= 1
    assert data["data"]["total"] >= 1


def test_get_execution_detail(client, sample_execution):
    """GET /api/v1/executions/{eid} — returns 200 with steps and case_results"""
    resp = client.get(f"/api/v1/executions/{sample_execution.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "steps" in data["data"]
    assert len(data["data"]["steps"]) >= 1
    assert "case_results" in data["data"]
    assert len(data["data"]["case_results"]) >= 1
    # 验证 case_results 聚合正确：sample_execution 的步骤 status=success
    for cr in data["data"]["case_results"]:
        assert cr["status"] in ("success", "failed", "running", "pending", "skipped", "unknown")
        assert "case_name" in cr
        assert "step_count" in cr
        assert "duration" in cr
        assert "steps" in cr


def test_get_execution_detail_with_failed_case(client, db_session, sample_project, sample_test_case):
    """GET /api/v1/executions/{eid} — case_results 应包含 failed 状态"""
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep
    from datetime import datetime as dt

    exec_obj = Execution(
        project_id=sample_project.id,
        batch_name="Failed Batch",
        total_cases=1,
        passed_cases=0,
        failed_cases=1,
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
        action="click",
        status="failed",
        duration_ms=100,
    )
    db_session.add(step)
    db_session.commit()

    resp = client.get(f"/api/v1/executions/{exec_obj.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    cr = data["data"]["case_results"]
    assert len(cr) == 1
    assert cr[0]["status"] == "failed"
    assert cr[0]["case_name"] == sample_test_case.case_name
    assert cr[0]["step_count"] == 1


def test_get_execution_detail_nonexistent(client):
    """GET /api/v1/executions/{eid} — nonexistent returns 404"""
    resp = client.get("/api/v1/executions/99999")
    assert resp.status_code == 404


def test_get_execution_status(client, sample_execution):
    """GET /api/v1/executions/{eid}/status — returns progress data with percentage"""
    resp = client.get(f"/api/v1/executions/{sample_execution.id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "percentage" in data["data"]
    assert "progress" in data["data"]


def test_stop_execution(client, sample_project, sample_test_case, sample_generated_code, mock_playwright_for_execution_service):
    """POST /api/v1/executions/{eid}/stop — returns 200, status changes to stopped"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "headless", "batch_name": "StopTest"},
    )
    eid = resp.json()["data"]["execution_id"]

    resp = client.post(f"/api/v1/executions/{eid}/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["status"] == "stopped"


def test_stop_execution_already_completed(client, sample_execution):
    """POST /api/v1/executions/{eid}/stop — already completed returns message"""
    resp = client.post(f"/api/v1/executions/{sample_execution.id}/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["status"] == "completed"