"""报告路由测试 — POST generate + GET report info"""

import pytest


class TestGenerateReport:
    """POST /api/v1/executions/{eid}/reports/generate"""

    def test_generate_report(self, client, sample_execution, mock_jinja_template, mock_file_ops):
        resp = client.post(f"/api/v1/executions/{sample_execution.id}/reports/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "report_id" in data["data"]
        assert "download_url" in data["data"]
        assert data["data"]["report_id"] is not None

    def test_generate_report_no_execution(self, client, mock_jinja_template, mock_file_ops):
        resp = client.post("/api/v1/executions/99999/reports/generate")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404
        assert "不存在" in data["message"]

    def test_generate_report_cached(self, client, sample_execution, mock_jinja_template, mock_file_ops):
        # First call — generate
        resp1 = client.post(f"/api/v1/executions/{sample_execution.id}/reports/generate")
        assert resp1.status_code == 200
        data1 = resp1.json()

        # Second call — should return cached
        resp2 = client.post(f"/api/v1/executions/{sample_execution.id}/reports/generate")
        assert resp2.status_code == 200
        data2 = resp2.json()

        assert data2["data"]["report_id"] == data1["data"]["report_id"]
        assert data2["data"]["download_url"] == data1["data"]["download_url"]


class TestGetReport:
    """GET /api/v1/executions/{eid}/reports"""

    def test_get_report(self, client, sample_execution, mock_jinja_template, mock_file_ops):
        # Generate first
        client.post(f"/api/v1/executions/{sample_execution.id}/reports/generate")

        resp = client.get(f"/api/v1/executions/{sample_execution.id}/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "report_id" in data["data"]
        assert "summary" in data["data"]
        assert "download_url" in data["data"]

    def test_get_report_not_found(self, client, sample_execution):
        # No report generated yet
        resp = client.get(f"/api/v1/executions/{sample_execution.id}/reports")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404
        assert "尚未生成" in data["message"]