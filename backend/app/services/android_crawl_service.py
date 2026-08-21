"""Android 页面元素抓取 — Appium UiAutomator2 屏幕元素提取

与 Web ElementService 完全独立，不包含 Playwright 逻辑。
采集字段保存到 PageElement 表（platform=android），metadata 字段存储 Android 特有属性。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree

from sqlalchemy.orm import Session

from app.config import settings
from app.models.element import PageElement
from app.models.project import Project
from app.exceptions import NotFoundException, ValidationException

logger = logging.getLogger("autopilot.android_crawl")


@dataclass
class AndroidCrawledElement:
    """Android 抓取结果的单条元素"""
    element_type: str
    class_name: str
    resource_id: Optional[str]
    content_desc: Optional[str]
    text: Optional[str]
    bounds: Optional[str]
    is_visible: int
    enabled: int
    clickable: int
    selector: str
    selector_type: str
    metadata: dict = field(default_factory=dict)
    db_id: int = 0
    created_at: Optional[str] = None


@dataclass
class AndroidCrawlResult:
    """一次 Android 抓取的完整结果"""
    crawled_count: int
    elements: list[AndroidCrawledElement]
    elapsed_ms: int
    error: Optional[str] = None


class AndroidCrawlService:
    """Android 元素抓取 — Appium 连接 + XML 解析 + 选择器生成"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 抓取入口
    # ═══════════════════════════════════════════════

    def crawl(self, project_id: int) -> AndroidCrawlResult:
        """触发 Android 屏幕元素抓取

        1. 连接 Appium Server
        2. 获取页面 XML 源码
        3. 解析 XML 提取可交互元素
        4. 生成选择器
        5. 清空旧数据 → 批量插入新数据
        """
        project = self._db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundException(f"项目 {project_id} 不存在")

        # 平台一致性校验：Android 抓取只允许 android 项目
        if getattr(project, "platform", "web") != "android":
            raise ValidationException(
                f"项目 platform={project.platform}，Android 元素抓取仅支持 platform=android 的项目"
            )

        logger.info("开始 Android 元素抓取: project_id=%s", project_id)

        start_ts = time.perf_counter()
        try:
            elements = self._extract_elements()
        except Exception as e:
            elapsed = int((time.perf_counter() - start_ts) * 1000)
            logger.exception("Android 页面抓取异常")
            return AndroidCrawlResult(
                crawled_count=0,
                elements=[],
                elapsed_ms=elapsed,
                error=str(e),
            )

        elapsed = int((time.perf_counter() - start_ts) * 1000)
        logger.info("Android 提取到 %d 个元素，耗时 %dms", len(elements), elapsed)

        # 清空旧的 Android 元素 → 批量插入
        self._db.query(PageElement).filter(
            PageElement.project_id == project_id,
            PageElement.platform == "android",
        ).delete()
        for el in elements:
            self._db.add(PageElement(
                project_id=project_id,
                element_type=el.element_type,
                tag_name=el.class_name,
                element_id=el.resource_id or "",
                name=el.content_desc or "",
                class_name=el.class_name,
                selector=el.selector,
                text_content=el.text,
                is_visible=el.is_visible,
                bounding_box=el.bounds,
                platform="android",
                selector_type=el.selector_type,
                element_metadata=json.dumps(el.metadata, ensure_ascii=False),
            ))
        self._db.commit()

        return AndroidCrawlResult(
            crawled_count=len(elements),
            elements=elements,
            elapsed_ms=elapsed,
        )

    # ═══════════════════════════════════════════════
    # Appium 核心提取
    # ═══════════════════════════════════════════════

    def _extract_elements(self) -> list[AndroidCrawledElement]:
        """连接 Appium，获取页面 XML 源码，解析并提取元素"""
        from appium import webdriver as appium_webdriver

        desired_caps = {
            "platformName": "Android",
            "automationName": "UiAutomator2",
            "noReset": True,
            "autoGrantPermissions": True,
        }

        driver = appium_webdriver.Remote(settings.APPIUM_URL, desired_caps)
        driver.implicitly_wait(5000)

        try:
            page_source = driver.page_source
        finally:
            try:
                driver.quit()
            except Exception:
                pass

        return self._parse_page_source(page_source)

    # ═══════════════════════════════════════════════
    # XML 解析
    # ═══════════════════════════════════════════════

    def _parse_page_source(self, xml: str) -> list[AndroidCrawledElement]:
        """解析 Appium 返回的 XML page source，提取可交互元素"""
        root = ElementTree.fromstring(xml)
        elements: list[AndroidCrawledElement] = []

        # 需要包含的子节点类型（可交互的 UI 控件）
        interactive_classes = frozenset({
            "android.widget.Button",
            "android.widget.EditText",
            "android.widget.TextView",
            "android.widget.ImageView",
            "android.widget.ImageButton",
            "android.widget.CheckBox",
            "android.widget.RadioButton",
            "android.widget.Switch",
            "android.widget.ToggleButton",
            "android.widget.Spinner",
            "android.widget.SeekBar",
            "android.widget.RatingBar",
            "android.widget.ProgressBar",
            "android.widget.ListView",
            "android.widget.GridView",
            "android.widget.HorizontalScrollView",
            "android.widget.ScrollView",
            "android.widget.LinearLayout",
            "android.widget.RelativeLayout",
            "android.widget.FrameLayout",
            "android.widget.CardView",
            "android.widget.RecyclerView",
            "android.view.View",
            "android.view.ViewGroup",
        })

        # 递归遍历
        index_counter: dict[str, int] = {}
        self._walk_node(root, elements, interactive_classes, index_counter)

        # 去重（相同 resource-id + text 视为重复）
        seen = set()
        deduped = []
        for el in elements:
            key = (el.resource_id or "", el.text or "", el.class_name, el.bounds or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(el)

        return deduped

    def _walk_node(
        self,
        node: ElementTree.Element,
        result: list[AndroidCrawledElement],
        interactive_classes: frozenset,
        index_counter: dict[str, int],
        depth: int = 0,
    ) -> None:
        """递归遍历 XML 节点，提取有效元素"""
        if depth > 50:
            return

        tag = node.tag
        # Appium XML 的 tag 是 class name
        if not tag or tag == "hierarchy":
            # 跳过根节点，继续遍历子节点
            for child in node:
                self._walk_node(child, result, interactive_classes, index_counter, depth + 1)
            return

        attrs = self._parse_attributes(node)

        # 检查是否在交互类列表中，或包含 clickable=true
        class_name = tag
        is_interactive = class_name in interactive_classes
        is_clickable = attrs.get("clickable") == "true"
        is_enabled = attrs.get("enabled") != "false"

        if not (is_interactive or is_clickable):
            # 继续遍历子节点
            for child in node:
                self._walk_node(child, result, interactive_classes, index_counter, depth + 1)
            return

        # 过滤不可见元素
        if attrs.get("visible") == "false":
            return

        resource_id = attrs.get("resource-id") or ""
        content_desc = attrs.get("content-desc") or ""
        text = attrs.get("text") or ""
        bounds = attrs.get("bounds") or ""

        # 生成选择器
        selector, selector_type = self._generate_selector(
            resource_id, content_desc, text, class_name, index_counter
        )

        # 构建 metadata
        metadata = {
            "resource_id": resource_id,
            "content_desc": content_desc,
            "class": class_name,
            "bounds": bounds,
            "enabled": 1 if is_enabled else 0,
        }

        el = AndroidCrawledElement(
            element_type=self._map_element_type(class_name, is_clickable),
            class_name=class_name,
            resource_id=resource_id or None,
            content_desc=content_desc or None,
            text=text or None,
            bounds=bounds or None,
            is_visible=1,
            enabled=1 if is_enabled else 0,
            clickable=1 if is_clickable else 0,
            selector=selector,
            selector_type=selector_type,
            metadata=metadata,
        )
        result.append(el)

        # 继续遍历子节点
        for child in node:
            self._walk_node(child, result, interactive_classes, index_counter, depth + 1)

    @staticmethod
    def _parse_attributes(node: ElementTree.Element) -> dict[str, str]:
        """从 XML 节点属性中提取 Android UI 属性（兼容两种命名风格）"""
        attrs = {}
        for key, val in node.attrib.items():
            # 去掉命名空间前缀，如 {http://schemas.android.com/apk/res/android}text
            short_key = key.split("}")[-1] if "}" in key else key
            # 统一命名
            normalized = short_key.replace("-", "_")
            attrs[normalized] = val
            # 保留原始短 key
            attrs[short_key] = val
        return attrs

    # ═══════════════════════════════════════════════
    # 选择器生成
    # ═══════════════════════════════════════════════

    @staticmethod
    def _generate_selector(
        resource_id: str,
        content_desc: str,
        text: str,
        class_name: str,
        index_counter: dict[str, int],
    ) -> tuple[str, str]:
        """按优先级生成 Android 选择器

        Priority:
          1. resource-id (accessibility id)
          2. content-desc (accessibility content description)
          3. text (XPath with text match)
          4. class + index (XPath fallback)
        """
        # 1. resource-id
        if resource_id:
            rid = resource_id.split("/")[-1] if "/" in resource_id else resource_id
            if rid:
                return f'id={rid}', "resource_id"

        # 2. content-desc
        if content_desc:
            escaped = content_desc.replace('"', '\\"')
            return f'accessibility_id={escaped}', "accessibility_id"

        # 3. text
        if text:
            escaped = text.replace('"', '\\"').replace("'", "\\'")
            return f'xpath=//{class_name}[@text="{escaped}"]', "xpath"

        # 4. class + index
        index_counter[class_name] = index_counter.get(class_name, -1) + 1
        idx = index_counter[class_name]
        return f'xpath=(//{class_name})[{idx + 1}]', "xpath"

    @staticmethod
    def _map_element_type(class_name: str, is_clickable: bool) -> str:
        """Android class 映射到通用元素类型"""
        if "Button" in class_name:
            return "button"
        if "EditText" in class_name:
            return "input"
        if "CheckBox" in class_name:
            return "checkbox"
        if "RadioButton" in class_name:
            return "radio"
        if "Switch" in class_name or "Toggle" in class_name:
            return "switch"
        if "Spinner" in class_name:
            return "select"
        if "Image" in class_name:
            return "image"
        if "TextView" in class_name or "Text" in class_name:
            return "text"
        if is_clickable:
            return "clickable"
        return "container"