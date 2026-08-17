"""Router tests for code generation endpoints — /api/v1/projects/{pid}/cases/..."""


def test_single_generate(client, sample_project, sample_test_case, mock_llm):
    """POST /api/v1/projects/{pid}/cases/{cid}/generate — returns 200 with code_id, is_valid"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/cases/{sample_test_case.id}/generate"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "code_id" in data["data"]
    assert data["data"]["is_valid"] is True


def test_single_generate_nonexistent_case(client, sample_project, mock_llm):
    """POST /api/v1/projects/{pid}/cases/{cid}/generate — nonexistent case returns error"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/cases/99999/generate"
    )
    assert resp.status_code == 500
    data = resp.json()
    assert data["code"] == 500


def test_batch_generate(client, sample_project, sample_test_case, mock_llm):
    """POST /api/v1/projects/{pid}/cases/generate-batch — returns 200 with batch_id, total"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/cases/generate-batch",
        json={"case_ids": [sample_test_case.id]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "batch_id" in data["data"]
    assert "total" in data["data"]
    assert data["data"]["total"] == 1
    assert data["data"]["status"] == "running"


def test_batch_generate_status(client, sample_project, sample_test_case, mock_llm):
    """GET /api/v1/projects/{pid}/generate-batch/{bid}/status — returns progress"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/cases/generate-batch",
        json={"case_ids": [sample_test_case.id]},
    )
    batch_id = resp.json()["data"]["batch_id"]

    resp = client.get(
        f"/api/v1/projects/{sample_project.id}/generate-batch/{batch_id}/status"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["batch_id"] == batch_id
    assert "progress_pct" in data["data"]


def test_batch_generate_status_unknown(client, sample_project):
    """GET /api/v1/projects/{pid}/generate-batch/{bid}/status — unknown batch returns error"""
    resp = client.get(
        f"/api/v1/projects/{sample_project.id}/generate-batch/nonexistent/status"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 404


def test_get_latest_code(client, sample_project, sample_test_case, sample_generated_code):
    """GET /api/v1/projects/{pid}/cases/{cid}/code — returns 200 with code content"""
    resp = client.get(
        f"/api/v1/projects/{sample_project.id}/cases/{sample_test_case.id}/code"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "code_content" in data["data"]


def test_get_latest_code_no_code(client, sample_project, sample_test_case):
    """GET /api/v1/projects/{pid}/cases/{cid}/code — no code returns error"""
    resp = client.get(
        f"/api/v1/projects/{sample_project.id}/cases/{sample_test_case.id}/code"
    )
    assert resp.status_code == 500
    data = resp.json()
    assert data["code"] == 500