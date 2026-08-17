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
    """GET /api/v1/executions/{eid} — returns 200 with steps"""
    resp = client.get(f"/api/v1/executions/{sample_execution.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "steps" in data["data"]
    assert len(data["data"]["steps"]) >= 1


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