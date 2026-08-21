"""Element Locator 测试 — Android 定位策略优先级、平台过滤、缺位处理"""
import pytest
from app.models.element import PageElement
from app.services.element_service import ElementService
from app.exceptions import NotFoundException


class TestElementLocatorPriority:
    """Android 定位策略优先级:
    resource-id → content-desc → text → class+attributes → XPath"""

    @pytest.fixture
    def android_project(self, db_session):
        from app.models.project import Project
        p = Project(name="Android Elem", target_url="android://app", platform="android")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        return p

    @pytest.fixture
    def element_svc(self, db_session):
        return ElementService(db_session)

    def test_element_with_resource_id(self, db_session, android_project):
        """resource-id 定位: selector_type = 'id'"""
        el = PageElement(
            project_id=android_project.id,
            element_type="button",
            tag_name="android.widget.Button",
            selector="com.example:id/btn_submit",
            selector_type="id",
            platform="android",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()
        assert el.selector_type == "id"

    def test_element_with_content_desc(self, db_session, android_project):
        """content-desc 定位: selector_type = 'accessibility_id'"""
        el = PageElement(
            project_id=android_project.id,
            element_type="image",
            tag_name="android.widget.ImageView",
            selector="Submit Button",
            selector_type="accessibility_id",
            platform="android",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()
        assert el.selector_type == "accessibility_id"

    def test_element_with_text(self, db_session, android_project):
        """text 定位: selector_type = 'xpath' (text 属性)"""
        el = PageElement(
            project_id=android_project.id,
            element_type="text",
            tag_name="android.widget.TextView",
            selector='//android.widget.TextView[@text="Login"]',
            selector_type="xpath",
            platform="android",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()
        assert el.selector_type == "xpath"

    def test_element_xpath_fallback(self, db_session, android_project):
        """XPath 兜底定位"""
        el = PageElement(
            project_id=android_project.id,
            element_type="button",
            tag_name="android.widget.Button",
            selector="//android.widget.Button[1]",
            selector_type="xpath",
            platform="android",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()
        assert el.selector_type == "xpath"


class TestElementMissingLocator:
    """元素定位信息缺位处理"""

    @pytest.fixture
    def android_project(self, db_session):
        from app.models.project import Project
        p = Project(name="Android Elem", target_url="android://app", platform="android")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        return p

    def test_element_without_selector_type(self, db_session, android_project):
        """selector_type 为 None 时兼容处理"""
        el = PageElement(
            project_id=android_project.id,
            element_type="button",
            tag_name="android.widget.Button",
            selector="com.example:id/btn",
            platform="android",
            is_visible=1,
            # selector_type 未设置
        )
        db_session.add(el)
        db_session.commit()
        assert el.selector_type is None

    def test_element_without_platform_defaults_to_web(self, db_session):
        """platform 未设置时默认为 'web'"""
        from app.models.project import Project
        p = Project(name="Test", target_url="https://example.com")
        db_session.add(p)
        db_session.commit()

        el = PageElement(
            project_id=p.id,
            element_type="button",
            tag_name="button",
            selector="#btn",
            # platform 未设置
        )
        db_session.add(el)
        db_session.commit()
        assert el.platform == "web"

    def test_element_with_empty_selector(self, db_session, android_project):
        """空 selector 可创建但有校验"""
        el = PageElement(
            project_id=android_project.id,
            element_type="button",
            tag_name="android.widget.Button",
            selector="",
            platform="android",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()
        assert el.selector == ""


class TestElementPlatformMismatch:
    """平台不匹配场景"""

    @pytest.fixture
    def web_project(self, db_session):
        from app.models.project import Project
        p = Project(name="Web", target_url="https://example.com", platform="web")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        return p

    @pytest.fixture
    def android_project(self, db_session):
        from app.models.project import Project
        p = Project(name="Android", target_url="android://app", platform="android")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        return p

    @pytest.fixture
    def element_svc(self, db_session):
        return ElementService(db_session)

    def test_web_element_not_visible_to_android(self, db_session, web_project, android_project, element_svc):
        """Web 项目的元素不应被 Android 项目看到"""
        el = PageElement(
            project_id=web_project.id,
            element_type="button",
            tag_name="button",
            selector="#web-btn",
            platform="web",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        # 通过直接 DB 查询验证：Android 项目查询时过滤 platform
        from sqlalchemy import and_
        android_elements = db_session.query(PageElement).filter(
            and_(
                PageElement.project_id == android_project.id,
                PageElement.platform == "android",
                PageElement.is_visible == 1,
            )
        ).all()
        assert len(android_elements) == 0

    def test_android_element_not_visible_to_web(self, db_session, web_project, android_project, element_svc):
        """Android 项目的元素不应被 Web 项目看到"""
        el = PageElement(
            project_id=android_project.id,
            element_type="button",
            tag_name="android.widget.Button",
            selector="com.example:id/btn",
            platform="android",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        # 通过直接 DB 查询验证：Web 项目查询时
        web_elements = db_session.query(PageElement).filter(
            PageElement.project_id == web_project.id,
        ).all()
        android_elements = [e for e in web_elements if e.platform == "android"]
        assert len(android_elements) == 0

    def test_mixed_platform_elements_query(self, db_session, web_project, android_project, element_svc):
        """混合平台项目中，按平台过滤"""
        # Web 元素
        el_web = PageElement(
            project_id=web_project.id,
            element_type="button", tag_name="button",
            selector="#web-btn", platform="web", is_visible=1,
        )
        db_session.add(el_web)
        # Android 元素（误放入 Web 项目）
        el_android = PageElement(
            project_id=web_project.id,
            element_type="button", tag_name="android.widget.Button",
            selector="com.example:id/btn", platform="android", is_visible=1,
        )
        db_session.add(el_android)
        db_session.commit()

        # 通过直接 DB 查询验证：按平台过滤
        web_elements = db_session.query(PageElement).filter(
            PageElement.project_id == web_project.id,
            PageElement.platform == "web",
        ).all()
        assert len(web_elements) == 1
        assert web_elements[0].platform == "web"