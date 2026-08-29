"""项目管理服务单元测试"""

import pytest
from app.services.project_service import ProjectService
from app.models.project import Project
from app.models.test_case import TestCase
from app.exceptions import NotFoundException, ValidationException


class TestProjectServiceCreate:
    def test_create_with_valid_data(self, db_session):
        service = ProjectService(db_session)
        project = service.create(
            name="Test Project",
            target_url="https://example.com",
            test_path="/login",
            browser_type="firefox",
            headless=False,
        )
        assert project.id is not None
        assert project.name == "Test Project"
        assert project.target_url == "https://example.com"
        assert project.test_path == "/login"
        assert project.browser_type == "firefox"
        assert project.headless == 0

    def test_create_with_name_and_target_url_only(self, db_session):
        service = ProjectService(db_session)
        project = service.create(
            name="Minimal Project",
            target_url="https://example.com",
        )
        assert project.id is not None
        assert project.name == "Minimal Project"
        assert project.target_url == "https://example.com"
        assert project.test_path == "/"
        assert project.browser_type == "chromium"
        assert project.headless == 1

    def test_create_invalid_browser_type(self, db_session):
        service = ProjectService(db_session)
        with pytest.raises(ValidationException, match="不支持的浏览器类型"):
            service.create(
                name="Bad Browser",
                target_url="https://example.com",
                browser_type="ie6",
            )

    def test_create_ssrf_banned_target_url_rejected(self, db_session):
        """SSRF 防护：元数据/受限地址作为 target_url → 拒绝创建"""
        service = ProjectService(db_session)
        with pytest.raises(ValidationException, match="target_url 校验失败"):
            service.create(
                name="SSRF",
                target_url="http://169.254.169.254/latest/meta-data",
            )

    def test_create_ssrf_non_http_scheme_rejected(self, db_session):
        service = ProjectService(db_session)
        with pytest.raises(ValidationException, match="target_url 校验失败"):
            service.create(
                name="SSRF",
                target_url="file:///etc/passwd",
            )

    def test_create_ssrf_non_default_port_rejected(self, db_session):
        service = ProjectService(db_session)
        with pytest.raises(ValidationException, match="target_url 校验失败"):
            service.create(
                name="SSRF",
                target_url="https://example.com:8080",
            )

    def test_create_ssrf_port_allowed_via_config(self, db_session):
        """config_json.allowed_ports 放行内网测试端口"""
        service = ProjectService(db_session)
        project = service.create(
            name="Internal",
            target_url="https://example.com:8080",
            config_json={"allowed_ports": [8080]},
        )
        assert project.target_url == "https://example.com:8080"

    def test_create_ssrf_android_not_checked(self, db_session):
        """Android 平台不校验 target_url（语义不同）"""
        service = ProjectService(db_session)
        project = service.create(
            name="Android App",
            target_url="http://169.254.169.254",
            platform="android",
        )
        assert project.platform == "android"


class TestProjectServiceGetOr404:
    def test_get_existing_project(self, db_session):
        project = Project(name="Project A", target_url="https://a.com")
        db_session.add(project)
        db_session.commit()

        service = ProjectService(db_session)
        result = service.get_or_404(project.id)
        assert result.id == project.id
        assert result.name == "Project A"

    def test_get_nonexistent_raises_not_found(self, db_session):
        service = ProjectService(db_session)
        with pytest.raises(NotFoundException, match="项目 99999 不存在"):
            service.get_or_404(99999)


class TestProjectServiceListPaginated:
    def test_with_multiple_projects(self, db_session):
        for i in range(5):
            db_session.add(Project(name=f"Project {i}", target_url=f"https://{i}.com"))
        db_session.commit()

        service = ProjectService(db_session)
        result = service.list_paginated(page=1, size=3)
        assert result.total == 5
        assert len(result.items) == 3
        assert result.page == 1
        assert result.size == 3
        assert result.pages == 2

    def test_with_zero_projects(self, db_session):
        service = ProjectService(db_session)
        result = service.list_paginated()
        assert result.total == 0
        assert result.items == []
        assert result.pages == 0

    def test_includes_case_count(self, db_session):
        project = Project(name="P", target_url="https://a.com")
        db_session.add(project)
        db_session.commit()

        for i in range(3):
            db_session.add(TestCase(
                project_id=project.id,
                case_name=f"Case {i}",
                steps="[]",
            ))
        db_session.commit()

        service = ProjectService(db_session)
        result = service.list_paginated()
        assert len(result.items) == 1
        assert result.items[0]["case_count"] == 3


class TestProjectServiceUpdate:
    def test_update_partial_fields(self, db_session):
        project = Project(
            name="Old Name",
            target_url="https://old.com",
            test_path="/old",
            browser_type="chromium",
            headless=1,
        )
        db_session.add(project)
        db_session.commit()

        service = ProjectService(db_session)
        updated = service.update(project.id, name="New Name", test_path="/new")
        assert updated.name == "New Name"
        assert updated.test_path == "/new"
        assert updated.target_url == "https://old.com"  # unchanged
        assert updated.browser_type == "chromium"  # unchanged

    def test_update_nonexistent_raises_not_found(self, db_session):
        service = ProjectService(db_session)
        with pytest.raises(NotFoundException):
            service.update(99999, name="X")

    def test_update_headless_bool_stored_as_int(self, db_session):
        project = Project(name="P", target_url="https://a.com", headless=1)
        db_session.add(project)
        db_session.commit()

        service = ProjectService(db_session)
        updated = service.update(project.id, headless=False)
        assert updated.headless == 0
        assert isinstance(updated.headless, int)

        updated2 = service.update(project.id, headless=True)
        assert updated2.headless == 1
        assert isinstance(updated2.headless, int)

    def test_update_ssrf_banned_target_url_rejected(self, db_session):
        """更新 target_url 到受限地址 → 拒绝"""
        project = Project(name="P", target_url="https://a.com")
        db_session.add(project)
        db_session.commit()

        service = ProjectService(db_session)
        with pytest.raises(ValidationException, match="target_url 校验失败"):
            service.update(project.id, target_url="http://0.0.0.0:80")

    def test_update_ssrf_valid_target_url_passes(self, db_session):
        project = Project(name="P", target_url="https://a.com")
        db_session.add(project)
        db_session.commit()

        service = ProjectService(db_session)
        updated = service.update(project.id, target_url="https://new.com")
        assert updated.target_url == "https://new.com"


class TestProjectServiceDelete:
    def test_delete_existing(self, db_session):
        project = Project(name="P", target_url="https://a.com")
        db_session.add(project)
        db_session.commit()
        pid = project.id

        service = ProjectService(db_session)
        result = service.delete(pid)
        assert result == pid

        assert db_session.query(Project).filter(Project.id == pid).first() is None

    def test_delete_nonexistent_raises_not_found(self, db_session):
        service = ProjectService(db_session)
        with pytest.raises(NotFoundException):
            service.delete(99999)