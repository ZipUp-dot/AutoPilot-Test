"""测试 app/routers/cases.py — 用例导入/查询/删除路由"""

import io
import json
from openpyxl import Workbook


class TestImportCases:
    """POST /api/v1/projects/{pid}/cases/import"""

    def test_import_excel_valid(self, client, sample_project):
        wb = Workbook()
        ws = wb.active
        ws.append(["用例名称", "操作步骤", "优先级"])
        ws.append([
            "Test Login",
            json.dumps([{"action": "click", "target": "#btn"}], ensure_ascii=False),
            "P0",
        ])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/cases/import",
            files={"file": ("test.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 1
        assert data["data"]["success"] == 1

    def test_import_no_file(self, client, sample_project):
        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/cases/import",
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == 422


class TestListCases:
    """GET /api/v1/projects/{pid}/cases/"""

    def test_list_cases(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/",
            params={"page": 1, "size": 20},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 1
        assert data["data"]["items"][0]["case_name"] == "Login Test"

    def test_filter_by_status(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/",
            params={"status": "imported"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        for item in data["data"]["items"]:
            assert item["status"] == "imported"

    def test_filter_by_status_no_match(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/",
            params={"status": "generated"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 0

    def test_filter_by_priority(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/",
            params={"priority": "P0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        for item in data["data"]["items"]:
            assert item["priority"] == "P0"

    def test_search_by_keyword(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/",
            params={"keyword": "Login"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 1

    def test_search_by_keyword_no_match(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/",
            params={"keyword": "zzz_nonexistent_zzz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 0


class TestGetCaseDetail:
    """GET /api/v1/projects/{pid}/cases/{cid}"""

    def test_get_detail(self, client, sample_project, sample_test_case):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/{sample_test_case.id}",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["case_name"] == "Login Test"
        assert isinstance(data["data"]["steps"], list)
        assert len(data["data"]["steps"]) >= 1

    def test_get_detail_nonexistent(self, client, sample_project):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/cases/99999",
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404


class TestDeleteCase:
    """DELETE /api/v1/projects/{pid}/cases/{cid}"""

    def test_delete_single(self, client, sample_project, sample_test_case):
        resp = client.delete(
            f"/api/v1/projects/{sample_project.id}/cases/{sample_test_case.id}",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["deleted"] == sample_test_case.id


class TestBatchDeleteCases:
    """DELETE /api/v1/projects/{pid}/cases/"""

    def test_batch_delete(self, client, sample_project, sample_test_case):
        resp = client.request(
            "DELETE",
            f"/api/v1/projects/{sample_project.id}/cases/",
            json={"ids": [sample_test_case.id]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["deleted_count"] >= 1