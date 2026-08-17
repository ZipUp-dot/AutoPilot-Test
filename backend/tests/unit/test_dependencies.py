"""Test app/dependencies.py — PaginationParams, paginate, get_project_or_404"""

import pytest

from app.dependencies import PaginationParams, paginate, get_project_or_404
from app.models.project import Project
from app.exceptions import NotFoundException


# ═══════════════════════════════════════════════
# PaginationParams
# ═══════════════════════════════════════════════

class TestPaginationParams:
    def test_defaults(self):
        params = PaginationParams(page=1, size=20)
        assert params.page == 1
        assert params.size == 20
        assert params.offset == 0

    def test_page_1_size_20_offset_0(self):
        params = PaginationParams(page=1, size=20)
        assert params.offset == 0

    def test_page_3_size_10_offset_20(self):
        params = PaginationParams(page=3, size=10)
        assert params.offset == 20

    def test_page_2_size_50_offset_50(self):
        params = PaginationParams(page=2, size=50)
        assert params.offset == 50

    def test_page_5_size_100_offset_400(self):
        params = PaginationParams(page=5, size=100)
        assert params.offset == 400

    def test_page_1_size_1_offset_0(self):
        params = PaginationParams(page=1, size=1)
        assert params.offset == 0

    def test_page_10_size_1000_offset_9000(self):
        params = PaginationParams(page=10, size=1000)
        assert params.offset == 9000


# ═══════════════════════════════════════════════
# paginate()
# ═══════════════════════════════════════════════

class TestPaginate:
    def test_paginate_25_items_page_1_size_10(self, db_session):
        for i in range(25):
            db_session.add(Project(name=f"P{i}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=1, size=10)
        result = paginate(db_session.query(Project), params)

        assert len(result["items"]) == 10
        assert result["total"] == 25
        assert result["page"] == 1
        assert result["size"] == 10
        assert result["pages"] == 3

    def test_paginate_25_items_page_3_size_10(self, db_session):
        for i in range(25):
            db_session.add(Project(name=f"P{i}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=3, size=10)
        result = paginate(db_session.query(Project), params)

        assert len(result["items"]) == 5  # 最后一页只有 5 条
        assert result["total"] == 25
        assert result["page"] == 3
        assert result["pages"] == 3

    def test_paginate_zero_items(self, db_session):
        params = PaginationParams(page=1, size=10)
        result = paginate(db_session.query(Project), params)

        assert result["items"] == []
        assert result["total"] == 0
        assert result["page"] == 1
        assert result["size"] == 10
        assert result["pages"] == 0

    def test_paginate_items_less_than_page_size(self, db_session):
        for i in range(3):
            db_session.add(Project(name=f"P{i}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=1, size=10)
        result = paginate(db_session.query(Project), params)

        assert len(result["items"]) == 3
        assert result["total"] == 3
        assert result["pages"] == 1

    def test_paginate_single_item_page_1_size_20(self, db_session):
        db_session.add(Project(name="Only", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=1, size=20)
        result = paginate(db_session.query(Project), params)

        assert len(result["items"]) == 1
        assert result["total"] == 1
        assert result["pages"] == 1

    def test_paginate_page_beyond_total_returns_empty(self, db_session):
        for i in range(10):
            db_session.add(Project(name=f"P{i}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=5, size=10)
        result = paginate(db_session.query(Project), params)

        assert result["items"] == []
        assert result["total"] == 10
        assert result["pages"] == 1

    def test_paginate_100_items_page_1_size_10(self, db_session):
        for i in range(100):
            db_session.add(Project(name=f"P{i}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=1, size=10)
        result = paginate(db_session.query(Project), params)

        assert len(result["items"]) == 10
        assert result["total"] == 100
        assert result["pages"] == 10

    def test_paginate_100_items_page_5_size_10(self, db_session):
        for i in range(100):
            db_session.add(Project(name=f"P{i}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=5, size=10)
        result = paginate(db_session.query(Project), params)

        assert len(result["items"]) == 10
        assert result["total"] == 100
        assert result["page"] == 5
        assert result["pages"] == 10

    def test_paginate_items_are_in_order(self, db_session):
        for i in range(10):
            db_session.add(Project(name=f"P{i:02d}", target_url="https://example.com"))
        db_session.commit()

        params = PaginationParams(page=1, size=5)
        result = paginate(db_session.query(Project).order_by(Project.name), params)

        names = [p.name for p in result["items"]]
        assert names == ["P00", "P01", "P02", "P03", "P04"]


# ═══════════════════════════════════════════════
# get_project_or_404
# ═══════════════════════════════════════════════

class TestGetProjectOr404:
    def test_existing_project_returns_project(self, db_session):
        project = Project(name="Test Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()

        result = get_project_or_404(project.id, db_session)
        assert result is project
        assert result.id == project.id
        assert result.name == "Test Project"

    def test_nonexistent_project_raises_not_found(self, db_session):
        with pytest.raises(NotFoundException) as exc_info:
            get_project_or_404(9999, db_session)
        assert exc_info.value.code == 404
        assert "项目 9999 不存在" in exc_info.value.message

    def test_multiple_existing_projects_returns_correct_one(self, db_session):
        p1 = Project(name="P1", target_url="https://example.com")
        p2 = Project(name="P2", target_url="https://example.com")
        p3 = Project(name="P3", target_url="https://example.com")
        db_session.add_all([p1, p2, p3])
        db_session.commit()

        result = get_project_or_404(p2.id, db_session)
        assert result.id == p2.id
        assert result.name == "P2"

    def test_deleted_project_raises_not_found(self, db_session):
        project = Project(name="Temp", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        project_id = project.id

        db_session.delete(project)
        db_session.commit()

        with pytest.raises(NotFoundException) as exc_info:
            get_project_or_404(project_id, db_session)
        assert exc_info.value.code == 404