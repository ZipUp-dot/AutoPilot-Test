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