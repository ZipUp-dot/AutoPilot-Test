"""测试 app/routers/projects.py — 项目 CRUD 路由"""


class TestListProjects:
    """GET /api/v1/projects/"""

    def test_empty_list(self, client):
        resp = client.get("/api/v1/projects/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["items"] == []
        assert data["data"]["total"] == 0

    def test_list_with_data(self, client, sample_project):
        resp = client.get("/api/v1/projects/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 1
        assert data["data"]["items"][0]["name"] == "Test Project"


class TestCreateProject:
    """POST /api/v1/projects/"""

    def test_create_valid(self, client):
        resp = client.post("/api/v1/projects/", json={
            "name": "Test Project",
            "target_url": "https://example.com",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Test Project"
        assert data["data"]["target_url"] == "https://example.com"
        assert "id" in data["data"]

    def test_create_empty_name(self, client):
        resp = client.post("/api/v1/projects/", json={
            "name": "",
            "target_url": "https://example.com",
        })
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == 422

    def test_create_missing_target_url(self, client):
        resp = client.post("/api/v1/projects/", json={
            "name": "No URL",
        })
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == 422


class TestGetProject:
    """GET /api/v1/projects/{id}"""

    def test_get_existing(self, client, sample_project):
        resp = client.get(f"/api/v1/projects/{sample_project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Test Project"
        assert data["data"]["id"] == sample_project.id

    def test_get_nonexistent(self, client):
        resp = client.get("/api/v1/projects/99999")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404


class TestUpdateProject:
    """PUT /api/v1/projects/{id}"""

    def test_update_partial(self, client, sample_project):
        resp = client.put(f"/api/v1/projects/{sample_project.id}", json={
            "name": "Updated Name",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Updated Name"
        # unchanged fields should remain
        assert data["data"]["target_url"] == "https://example.com"

    def test_update_nonexistent(self, client):
        resp = client.put("/api/v1/projects/99999", json={
            "name": "Ghost",
        })
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404


class TestDeleteProject:
    """DELETE /api/v1/projects/{id}"""

    def test_delete_existing(self, client, sample_project):
        resp = client.delete(f"/api/v1/projects/{sample_project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["deleted"] is True

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/v1/projects/99999")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404