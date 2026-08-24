"""测试 app/routers/elements.py — 元素抓取与查询路由"""


class TestCrawlElements:
    """POST /api/v1/projects/{pid}/elements/crawl"""

    def test_crawl_elements(self, client, sample_project, mock_playwright_for_element_service):
        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/elements/crawl",
            json={"max_depth": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "crawled_count" in data["data"]
        assert "url" in data["data"]
        assert "elapsed_ms" in data["data"]

    def test_crawl_nonexistent_project(self, client, mock_playwright_for_element_service):
        resp = client.post(
            "/api/v1/projects/99999/elements/crawl",
            json={"max_depth": 1},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404


class TestCrawlAndroidElements:
    """POST /api/v1/projects/{pid}/elements/crawl/android"""

    def _make_result(self, error=None):
        from app.services.android_crawl_service import (
            AndroidCrawlResult,
            AndroidCrawledElement,
        )
        elements = []
        if not error:
            elements = [AndroidCrawledElement(
                element_type="button",
                class_name="android.widget.Button",
                resource_id="com.example:id/btn",
                content_desc=None,
                text="登录",
                bounds="[0,0][10,10]",
                is_visible=1,
                enabled=1,
                clickable=1,
                selector="id=btn",
                selector_type="resource_id",
                metadata={},
            )]
        return AndroidCrawlResult(
            crawled_count=len(elements),
            elements=elements,
            elapsed_ms=10,
            error=error,
        )

    def test_crawl_android_success(self, client, sample_project, mocker):
        mock_svc = mocker.patch("app.routers.elements.AndroidCrawlService")
        mock_svc.return_value.crawl.return_value = self._make_result()

        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/elements/crawl/android",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["crawled_count"] == 1
        assert len(data["data"]["elements"]) == 1
        assert data["data"]["elements"][0]["selector"] == "id=btn"
        assert data["data"]["elements"][0]["selector_type"] == "resource_id"
        assert data["data"]["elements"][0]["resource_id"] == "com.example:id/btn"

    def test_crawl_android_result_error(self, client, sample_project, mocker):
        mock_svc = mocker.patch("app.routers.elements.AndroidCrawlService")
        mock_svc.return_value.crawl.return_value = self._make_result(error="device offline")

        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/elements/crawl/android",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 500
        assert data["message"] == "device offline"
        assert data["data"]["crawled_count"] == 0

    def test_crawl_android_exception(self, client, sample_project, mocker):
        """路由未捕获异常 → 直接抛出（由全局异常处理器兜底返回 500）"""
        import pytest
        mock_svc = mocker.patch("app.routers.elements.AndroidCrawlService")
        mock_svc.return_value.crawl.side_effect = RuntimeError("connection refused")

        # TestClient 默认 raise_server_exceptions=True，异常直接传播到测试
        with pytest.raises(RuntimeError, match="connection refused"):
            client.post(
                f"/api/v1/projects/{sample_project.id}/elements/crawl/android",
            )


class TestListElements:
    """GET /api/v1/projects/{pid}/elements/"""

    def test_list_elements(self, client, sample_project, sample_page_element):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/elements/",
            params={"page": 1, "size": 20},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 1
        items = data["data"]["items"]
        assert items[0]["element_type"] == "button"

    def test_list_nonexistent_project(self, client):
        resp = client.get("/api/v1/projects/99999/elements/")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404

    def test_filter_by_element_type(self, client, sample_project, sample_page_element):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/elements/",
            params={"element_type": "button"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        for item in data["data"]["items"]:
            assert item["element_type"] == "button"

    def test_filter_by_keyword(self, client, sample_project, sample_page_element):
        # sample_page_element has text_content="Submit"
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/elements/",
            params={"keyword": "Submit"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 1

    def test_filter_by_keyword_no_match(self, client, sample_project, sample_page_element):
        resp = client.get(
            f"/api/v1/projects/{sample_project.id}/elements/",
            params={"keyword": "zzz_nonexistent_zzz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 0


class TestClearElements:
    """DELETE /api/v1/projects/{pid}/elements/"""

    def test_clear_elements(self, client, sample_project, sample_page_element):
        resp = client.delete(
            f"/api/v1/projects/{sample_project.id}/elements/",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["deleted_count"] >= 1

    def test_clear_nonexistent_project(self, client):
        resp = client.delete("/api/v1/projects/99999/elements/")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404