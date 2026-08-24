"""AndroidCrawlService 测试 — XML 解析 + 选择器生成 + crawl 入口

Appium 真实连接被 mock，纯 XML/字符串逻辑全部实测。
覆盖:
  - crawl(): 平台校验 / DB 插入 / 异常兜底
  - _extract_elements(): Appium 连接 + quit 兜底
  - _parse_page_source(): XML 解析 + 去重
  - _walk_node(): 递归遍历 / 交互类 / visible 过滤 / depth 上限
  - _parse_attributes(): 命名空间剥离 + 连字符归一化
  - _generate_selector(): 4 级降级（resource-id → content-desc → text → class+index）
  - _map_element_type(): 全部分类映射
"""

import json
import sys
from xml.etree import ElementTree

import pytest

from app.exceptions import NotFoundException, ValidationException
from app.services.android_crawl_service import (
    AndroidCrawlService,
    AndroidCrawledElement,
)
from app.models.element import PageElement
from app.models.project import Project


# ── 测试样本 ──

SAMPLE_PAGE_SOURCE = '''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <android.widget.FrameLayout resource-id="com.example.app:id/root" bounds="[0,0][1080,1920]">
    <android.widget.Button resource-id="com.example.app:id/btn_login" text="登录"
      content-desc="login button" clickable="true" enabled="true"
      bounds="[100,500][500,700]"/>
    <android.widget.EditText resource-id="com.example.app:id/input_user" text=""
      enabled="true" bounds="[100,300][500,400]"/>
    <android.widget.TextView text="欢迎" enabled="true" bounds="[100,100][500,200]"/>
    <android.widget.ImageView bounds="[0,0][50,50]"/>
    <android.view.View visible="false">
      <android.widget.Button text="隐藏按钮" clickable="true"/>
    </android.view.View>
  </android.widget.FrameLayout>
</hierarchy>'''


def _android_project(db_session) -> Project:
    """创建 android 平台项目"""
    project = Project(
        name="Android App",
        target_url="https://example.com",
        platform="android",
        status="active",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ═══════════════════════════════════════════════
# _map_element_type()
# ═══════════════════════════════════════════════

class TestMapElementType:
    """_map_element_type() 分类映射"""

    def test_button(self):
        assert AndroidCrawlService._map_element_type("android.widget.Button", False) == "button"

    def test_edit_text(self):
        assert AndroidCrawlService._map_element_type("android.widget.EditText", False) == "input"

    def test_checkbox(self):
        assert AndroidCrawlService._map_element_type("android.widget.CheckBox", False) == "checkbox"

    def test_radio_button(self):
        """RadioButton 含 'Button' 子串 → 现有优先级下命中 button（radio 分支不可达）"""
        assert AndroidCrawlService._map_element_type("android.widget.RadioButton", False) == "button"

    def test_switch(self):
        assert AndroidCrawlService._map_element_type("android.widget.Switch", False) == "switch"

    def test_toggle_button(self):
        """ToggleButton 含 'Button' 子串 → 现有优先级下命中 button（switch 分支不可达）"""
        assert AndroidCrawlService._map_element_type("android.widget.ToggleButton", False) == "button"

    def test_spinner(self):
        assert AndroidCrawlService._map_element_type("android.widget.Spinner", False) == "select"

    def test_image(self):
        assert AndroidCrawlService._map_element_type("android.widget.ImageView", False) == "image"

    def test_text_view(self):
        assert AndroidCrawlService._map_element_type("android.widget.TextView", False) == "text"

    def test_clickable_fallback(self):
        assert AndroidCrawlService._map_element_type("android.view.ViewGroup", True) == "clickable"

    def test_container_default(self):
        assert AndroidCrawlService._map_element_type("android.widget.LinearLayout", False) == "container"


# ═══════════════════════════════════════════════
# _generate_selector()
# ═══════════════════════════════════════════════

class TestGenerateSelector:
    """_generate_selector() 4 级降级策略"""

    def test_resource_id_priority(self):
        sel, stype = AndroidCrawlService._generate_selector(
            "com.example.app:id/btn", "", "", "android.widget.Button", {}
        )
        assert sel == "id=btn"
        assert stype == "resource_id"

    def test_resource_id_without_slash(self):
        sel, stype = AndroidCrawlService._generate_selector(
            "btn_login", "", "", "android.widget.Button", {}
        )
        assert sel == "id=btn_login"
        assert stype == "resource_id"

    def test_content_desc_fallback(self):
        sel, stype = AndroidCrawlService._generate_selector(
            "", "返回上一页", "", "android.widget.Button", {}
        )
        assert sel == "accessibility_id=返回上一页"
        assert stype == "accessibility_id"

    def test_content_desc_escapes_quote(self):
        sel, _ = AndroidCrawlService._generate_selector(
            "", '说"你好"', "", "android.widget.Button", {}
        )
        assert sel == 'accessibility_id=说\\"你好\\"'

    def test_text_xpath_fallback(self):
        sel, stype = AndroidCrawlService._generate_selector(
            "", "", "登录", "android.widget.Button", {}
        )
        assert sel == 'xpath=//android.widget.Button[@text="登录"]'
        assert stype == "xpath"

    def test_text_xpath_escapes_quotes(self):
        sel, _ = AndroidCrawlService._generate_selector(
            "", "", "it's", "android.widget.Button", {}
        )
        assert sel == "xpath=//android.widget.Button[@text=\"it\\'s\"]"

    def test_class_index_last_resort(self):
        counter: dict[str, int] = {}
        sel1, stype = AndroidCrawlService._generate_selector(
            "", "", "", "android.widget.Button", counter
        )
        sel2, _ = AndroidCrawlService._generate_selector(
            "", "", "", "android.widget.Button", counter
        )
        assert sel1 == "xpath=(//android.widget.Button)[1]"
        assert sel2 == "xpath=(//android.widget.Button)[2]"
        assert stype == "xpath"


# ═══════════════════════════════════════════════
# _parse_attributes()
# ═══════════════════════════════════════════════

class TestParseAttributes:
    """_parse_attributes() 属性归一化"""

    def test_hyphen_and_plain_attributes(self):
        node = ElementTree.fromstring(
            '<node resource-id="com.example:id/btn" text="登录" clickable="true"/>'
        )
        attrs = AndroidCrawlService._parse_attributes(node)
        assert attrs["resource-id"] == "com.example:id/btn"  # 原始短 key
        assert attrs["resource_id"] == "com.example:id/btn"  # 连字符归一化
        assert attrs["text"] == "登录"
        assert attrs["clickable"] == "true"

    def test_namespace_prefix_stripped(self):
        node = ElementTree.fromstring(
            '<node xmlns:android="http://schemas.android.com/apk/res/android" '
            'android:text="hello" android:resource-id="com.example:id/x"/>'
        )
        attrs = AndroidCrawlService._parse_attributes(node)
        assert attrs["text"] == "hello"
        assert attrs["resource-id"] == "com.example:id/x"


# ═══════════════════════════════════════════════
# _parse_page_source() / _walk_node()
# ═══════════════════════════════════════════════

class TestParsePageSource:
    """_parse_page_source() XML 解析 + _walk_node() 递归遍历"""

    def test_parse_basic_elements(self, db_session):
        svc = AndroidCrawlService(db_session)
        elements = svc._parse_page_source(SAMPLE_PAGE_SOURCE)
        # FrameLayout(交互类) + Button + EditText + TextView + ImageView = 5
        # View visible=false → 跳过（且其子节点不遍历）
        assert len(elements) == 5

        btn = next(e for e in elements if e.resource_id == "com.example.app:id/btn_login")
        assert btn.selector == "id=btn_login"
        assert btn.selector_type == "resource_id"
        assert btn.clickable == 1
        assert btn.enabled == 1
        assert btn.text == "登录"
        assert btn.content_desc == "login button"
        assert btn.bounds == "[100,500][500,700]"
        assert btn.element_type == "button"
        assert btn.is_visible == 1

        tv = next(e for e in elements if e.text == "欢迎")
        assert tv.selector == 'xpath=//android.widget.TextView[@text="欢迎"]'
        assert tv.selector_type == "xpath"
        assert tv.element_type == "text"

    def test_parse_empty_hierarchy(self, db_session):
        svc = AndroidCrawlService(db_session)
        assert svc._parse_page_source("<hierarchy></hierarchy>") == []

    def test_parse_invalid_xml_raises(self, db_session):
        svc = AndroidCrawlService(db_session)
        with pytest.raises(ElementTree.ParseError):
            svc._parse_page_source("not xml at all")

    def test_dedup_identical_elements(self, db_session):
        svc = AndroidCrawlService(db_session)
        xml = (
            "<hierarchy>"
            '<android.widget.Button resource-id="com.example:id/btn" text="A" '
            'clickable="true" bounds="[0,0][10,10]"/>'
            '<android.widget.Button resource-id="com.example:id/btn" text="A" '
            'clickable="true" bounds="[0,0][10,10]"/>'
            "</hierarchy>"
        )
        elements = svc._parse_page_source(xml)
        assert len(elements) == 1

    def test_clickable_non_interactive_class_included(self, db_session):
        """非交互类但 clickable=true → 仍保留"""
        svc = AndroidCrawlService(db_session)
        xml = (
            "<hierarchy>"
            '<android.webkit.WebView clickable="true" text="web area"/>'
            "</hierarchy>"
        )
        elements = svc._parse_page_source(xml)
        assert len(elements) == 1
        assert elements[0].class_name == "android.webkit.WebView"

    def test_depth_limit_skips_deep_nodes(self, db_session):
        """超过 50 层深度的节点被跳过（防止栈溢出）"""
        svc = AndroidCrawlService(db_session)
        # 每层 FrameLayout 带唯一 text，避免被去重合并
        inner = '<android.widget.Button clickable="true" text="leaf"/>'
        for d in range(55):
            inner = f'<android.widget.FrameLayout text="L{d}">{inner}</android.widget.FrameLayout>'
        elements = svc._parse_page_source(f"<hierarchy>{inner}</hierarchy>")
        # 55 层 FrameLayout 中 depth 1-50 保留（50 个），depth >50 及其子节点跳过
        assert len(elements) == 50


# ═══════════════════════════════════════════════
# _extract_elements()
# ═══════════════════════════════════════════════

class TestExtractElements:
    """_extract_elements() Appium 连接（mock）"""

    def _mock_appium(self, mocker, driver):
        mock_remote = mocker.MagicMock(return_value=driver)
        mock_webdriver = mocker.MagicMock()
        mock_webdriver.Remote = mock_remote
        mock_appium = mocker.MagicMock()
        mock_appium.webdriver = mock_webdriver
        mocker.patch.dict(sys.modules, {
            "appium": mock_appium,
            "appium.webdriver": mock_webdriver,
        })
        return mock_remote

    def test_extract_elements_success(self, db_session, mocker):
        driver = mocker.MagicMock()
        driver.page_source = SAMPLE_PAGE_SOURCE
        mock_remote = self._mock_appium(mocker, driver)

        svc = AndroidCrawlService(db_session)
        elements = svc._extract_elements()

        mock_remote.assert_called_once()
        driver.implicitly_wait.assert_called_once_with(5000)
        driver.quit.assert_called_once()
        assert len(elements) == 5

    def test_extract_elements_quit_failure_ignored(self, db_session, mocker):
        driver = mocker.MagicMock()
        driver.page_source = "<hierarchy></hierarchy>"
        driver.quit.side_effect = RuntimeError("quit failed")
        self._mock_appium(mocker, driver)

        svc = AndroidCrawlService(db_session)
        assert svc._extract_elements() == []  # quit 异常被吞掉


# ═══════════════════════════════════════════════
# crawl()
# ═══════════════════════════════════════════════

class TestCrawl:
    """crawl() 入口"""

    def test_crawl_project_not_found(self, db_session):
        svc = AndroidCrawlService(db_session)
        with pytest.raises(NotFoundException, match="项目 99999 不存在"):
            svc.crawl(99999)

    def test_crawl_rejects_web_project(self, db_session, sample_project):
        svc = AndroidCrawlService(db_session)
        with pytest.raises(ValidationException, match="仅支持 platform=android"):
            svc.crawl(sample_project.id)

    def test_crawl_success_saves_elements(self, db_session, mocker):
        project = _android_project(db_session)
        svc = AndroidCrawlService(db_session)

        fake_elements = [AndroidCrawledElement(
            element_type="button",
            class_name="android.widget.Button",
            resource_id="com.example.app:id/btn",
            content_desc=None,
            text="登录",
            bounds="[100,500][500,700]",
            is_visible=1,
            enabled=1,
            clickable=1,
            selector="id=btn",
            selector_type="resource_id",
            metadata={
                "resource_id": "com.example.app:id/btn",
                "class": "android.widget.Button",
                "bounds": "[100,500][500,700]",
                "enabled": 1,
            },
        )]
        mocker.patch.object(svc, "_extract_elements", return_value=fake_elements)

        result = svc.crawl(project.id)

        assert result.crawled_count == 1
        assert result.error is None
        assert len(result.elements) == 1
        assert result.elapsed_ms >= 0

        row = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == project.id)
            .first()
        )
        assert row is not None
        assert row.platform == "android"
        assert row.selector == "id=btn"
        assert row.selector_type == "resource_id"
        assert row.tag_name == "android.widget.Button"
        assert row.element_id == "com.example.app:id/btn"
        assert row.text_content == "登录"
        assert row.is_visible == 1
        assert json.loads(row.element_metadata)["class"] == "android.widget.Button"

    def test_crawl_clears_old_android_elements(self, db_session, mocker):
        project = _android_project(db_session)
        db_session.add(PageElement(
            project_id=project.id,
            element_type="text",
            tag_name="android.widget.TextView",
            selector="id=old",
            text_content="旧数据",
            platform="android",
        ))
        db_session.commit()

        svc = AndroidCrawlService(db_session)
        mocker.patch.object(svc, "_extract_elements", return_value=[])

        svc.crawl(project.id)

        old = (
            db_session.query(PageElement)
            .filter(PageElement.project_id == project.id)
            .all()
        )
        assert old == []  # 旧数据被清空

    def test_crawl_extract_error_returns_result(self, db_session, mocker):
        project = _android_project(db_session)
        svc = AndroidCrawlService(db_session)
        mocker.patch.object(svc, "_extract_elements", side_effect=RuntimeError("device offline"))

        result = svc.crawl(project.id)

        assert result.crawled_count == 0
        assert result.elements == []
        assert result.error == "device offline"
