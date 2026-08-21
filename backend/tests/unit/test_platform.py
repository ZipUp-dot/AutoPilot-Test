"""Platform 测试 — 验证项目创建、platform 校验、更新保护"""
import pytest
from app.services.project_service import ProjectService
from app.exceptions import ValidationException


class TestPlatformCreate:
    """创建项目时的 platform 校验"""

    def test_create_web_project(self, db_session):
        """创建 Web 平台项目"""
        svc = ProjectService(db_session)
        project = svc.create(name="Web Project", target_url="https://example.com", platform="web")
        assert project.platform == "web"

    def test_create_android_project(self, db_session):
        """创建 Android 平台项目"""
        svc = ProjectService(db_session)
        project = svc.create(name="Android Project", target_url="android://app", platform="android")
        assert project.platform == "android"

    def test_create_unknown_platform_raises(self, db_session):
        """未知平台抛出 ValidationException"""
        svc = ProjectService(db_session)
        with pytest.raises(ValidationException, match="不支持的平台类型"):
            svc.create(name="Bad Platform", target_url="https://example.com", platform="unknown")

    def test_invalid_platform_raises(self, db_session):
        """无效 platform 值抛出 ValidationException"""
        svc = ProjectService(db_session)
        with pytest.raises(ValidationException, match="不支持的平台类型"):
            svc.create(name="Bad", target_url="https://example.com", platform="ios")

    def test_create_default_platform(self, db_session):
        """默认 platform 为 web"""
        svc = ProjectService(db_session)
        project = svc.create(name="Default", target_url="https://example.com")
        assert project.platform == "web"


class TestPlatformUpdateProtection:
    """更新项目时 platform 只读保护"""

    def test_cannot_update_platform_web_to_android(self, db_session):
        """Web 项目不能通过 update 改为 Android"""
        svc = ProjectService(db_session)
        project = svc.create(name="Web Only", target_url="https://example.com", platform="web")
        # 尝试更新 platform
        svc.update(project.id, platform="android")
        db_session.refresh(project)
        # platform 不变
        assert project.platform == "web"

    def test_cannot_update_platform_android_to_web(self, db_session):
        """Android 项目不能通过 update 改为 Web"""
        svc = ProjectService(db_session)
        project = svc.create(name="Android Only", target_url="android://app", platform="android")
        svc.update(project.id, platform="web")
        db_session.refresh(project)
        assert project.platform == "android"

    def test_other_fields_still_updatable(self, db_session):
        """其他字段不受 platform 只读影响"""
        svc = ProjectService(db_session)
        project = svc.create(name="Original", target_url="https://example.com", platform="web")
        svc.update(project.id, name="Updated Name", headless=False)
        db_session.refresh(project)
        assert project.name == "Updated Name"
        assert project.headless == 0
        assert project.platform == "web"  # 保持不变

    def test_platform_immutable_after_persist(self, db_session):
        """已持久化的项目 platform 不可通过 update 修改"""
        svc = ProjectService(db_session)
        project = svc.create(name="Persist", target_url="https://example.com", platform="web")
        project_id = project.id
        # 多次更新尝试
        for _ in range(3):
            svc.update(project_id, platform="android")
        db_session.refresh(project)
        assert project.platform == "web"


class TestPlatformQuery:
    """platform 查询相关"""

    def test_list_contains_platform(self, db_session):
        """项目列表应包含 platform 字段"""
        svc = ProjectService(db_session)
        svc.create(name="Web", target_url="https://example.com", platform="web")
        svc.create(name="Android", target_url="android://app", platform="android")
        result = svc.list_paginated(page=1, size=10)
        platforms = {item["name"]: item["platform"] for item in result.items}
        assert platforms["Web"] == "web"
        assert platforms["Android"] == "android"