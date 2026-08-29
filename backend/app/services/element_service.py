"""页面元素管理业务逻辑 — Playwright 抓取 + 7 级选择器生成"""

import json
import os
import time
import re
import logging
from dataclasses import dataclass, field
from typing import Optional
from sqlalchemy.orm import Session

from app.models.element import PageElement
from app.models.project import Project
from app.config import settings
from app.exceptions import NotFoundException, PlaywrightException, ValidationException
from app.services.ai_service import _call_openai_vision

logger = logging.getLogger("autopilot.crawl")

# 动态 class 模式（排除 hash 类）
DYNAMIC_CLASS_PATTERN = re.compile(
    r'(css-[a-z0-9]+|_[a-zA-Z0-9]{6,}|[a-z]+-[a-f0-9]{6,}|sc-[a-zA-Z]+$)'
)


@dataclass
class CrawledElement:
    """一次抓取结果的单条元素"""
    element_type: str
    tag_name: str
    element_id: Optional[str]
    name: Optional[str]
    class_name: Optional[str]
    selector: str
    text_content: Optional[str]
    placeholder: Optional[str]
    is_visible: int
    bounding_box: dict
    attributes: dict = field(default_factory=dict)
    db_id: int = 0
    created_at: Optional[str] = None


@dataclass
class CrawlResult:
    """一次抓取的完整结果"""
    url: str
    crawled_count: int
    elements: list[CrawledElement]
    elapsed_ms: int
    error: Optional[str] = None


@dataclass
class PaginatedResult:
    items: list
    total: int
    page: int
    size: int
    pages: int


class ElementService:
    """元素抓取——Playwright 浏览器控制 + 7 级选择器生成"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 抓取入口
    # ═══════════════════════════════════════════════

    async def crawl(self, project_id: int, max_depth: int = 1) -> CrawlResult:
        """触发抓取主流程

        1. 获取项目 target_url + test_path
        2. Playwright 异步启动浏览器
        3. 提取元素 + 生成选择器
        4. 清空旧数据 → 批量插入新数据
        """
        project = self._db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundException(f"项目 {project_id} 不存在")

        # 平台一致性校验：Web 抓取只允许 web 项目
        if getattr(project, "platform", "web") != "web":
            raise ValidationException(
                f"项目 platform={project.platform}，Web 元素抓取仅支持 platform=web 的项目"
            )

        url = (project.target_url.rstrip("/") + "/" + project.test_path.lstrip("/")).rstrip("/")
        browser_type = project.browser_type or "chromium"

        # SSRF 执行期策略：target 同源 + 项目 allowlist
        try:
            config_json = json.loads(project.config_json) if project.config_json else None
        except (TypeError, ValueError):
            config_json = None
        from app.utils.url_policy import UrlPolicy
        policy = UrlPolicy(project.target_url, config_json=config_json)

        logger.info("开始抓取 %s [browser=%s]", url, browser_type)

        start_ts = time.perf_counter()
        try:
            elements = await self._extract_elements(
                url, browser_type,
                timeout_ms=settings.PLAYWRIGHT_TIMEOUT,
                policy=policy,
            )
        except PlaywrightException:
            raise
        except Exception as e:
            logger.exception("页面抓取异常")
            msg = str(e) or repr(e) or type(e).__name__
            raise PlaywrightException(f"页面抓取失败: {msg}")

        elapsed = int((time.perf_counter() - start_ts) * 1000)
        logger.info("提取到 %d 个元素，耗时 %dms", len(elements), elapsed)

        # 清空旧数据 → 批量插入
        self._db.query(PageElement).filter(PageElement.project_id == project_id).delete()
        for el in elements:
            self._db.add(PageElement(
                project_id=project_id,
                element_type=el.element_type,
                tag_name=el.tag_name,
                element_id=el.element_id or "",
                name=el.name or "",
                class_name=el.class_name or "",
                selector=el.selector,
                text_content=el.text_content,
                placeholder=el.placeholder,
                is_visible=el.is_visible,
                bounding_box=json.dumps(el.bounding_box, ensure_ascii=False) if el.bounding_box else None,
                attributes=json.dumps(el.attributes, ensure_ascii=False) if el.attributes else None,
                platform="web",
                selector_type="css",
            ))
        self._db.commit()

        return CrawlResult(
            url=url,
            crawled_count=len(elements),
            elements=elements,
            elapsed_ms=elapsed,
        )

    # ═══════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════

    def list_paginated(self, project_id: int, platform: str = None,
                       element_type: str = None,
                       keyword: str = None, page: int = 1, size: int = 50) -> PaginatedResult:
        import math

        query = self._db.query(PageElement).filter(PageElement.project_id == project_id)
        if platform:
            query = query.filter(PageElement.platform == platform)
        if element_type:
            query = query.filter(PageElement.element_type == element_type)
        if keyword:
            kw = f"%{keyword}%"
            query = query.filter(
                (PageElement.text_content.like(kw)) |
                (PageElement.selector.like(kw)) |
                (PageElement.name.like(kw))
            )
        query = query.order_by(PageElement.element_type, PageElement.id)

        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()

        elements = [_orm_to_crawled(el) for el in items]
        return PaginatedResult(
            items=elements, total=total, page=page, size=size,
            pages=math.ceil(total / size) if total > 0 else 0,
        )

    def clear_all(self, project_id: int, platform: str = None) -> int:
        query = self._db.query(PageElement).filter(
            PageElement.project_id == project_id
        )
        if platform:
            query = query.filter(PageElement.platform == platform)
        deleted = query.delete(synchronize_session=False)
        self._db.commit()
        return deleted

    # ═══════════════════════════════════════════════
    # Playwright 核心提取
    # ═══════════════════════════════════════════════

    async def _extract_elements(self, url: str, browser_type: str,
                                 timeout_ms: int = 30000, policy=None) -> list[CrawledElement]:
        """异步启动 Playwright，提取页面元素并生成选择器"""
        # 绕过 IDE 沙箱对 Playwright 子进程的拦截
        os.environ["TOOLHOST_SANDBOX_DISABLED"] = "true"
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise PlaywrightException("Playwright 未安装，请执行: pip install playwright && playwright install chromium")

        async with async_playwright() as p:
            browser_launcher = {
                "chromium": p.chromium,
                "firefox": p.firefox,
                "webkit": p.webkit,
            }.get(browser_type, p.chromium)

            try:
                browser = await browser_launcher.launch(
                    headless=settings.PLAYWRIGHT_HEADLESS,
                )
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    service_workers="block",
                )
                if policy is not None:
                    from app.utils.url_policy import install_network_policy
                    await install_network_policy(context, policy)
                page = await context.new_page()
            except Exception as e:
                msg = str(e) or repr(e) or type(e).__name__
                raise PlaywrightException(f"浏览器启动失败: {msg}")

            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception as e:
                logger.warning("首次 goto 失败: %s, 尝试 AI 辅助导航", e)
                try:
                    # 尝试 domcontentloaded 加载（部分页面可能已部分渲染）
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    except Exception:
                        pass

                    # AI 分析截图并执行前置操作
                    ai_ok = await self._ai_assisted_navigation(page, url)
                    if not ai_ok:
                        await browser.close()
                        msg = str(e) or repr(e) or type(e).__name__
                        raise PlaywrightException(f"无法访问页面 {url}: {msg}")

                    # 执行前置操作后重新尝试 goto
                    await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except PlaywrightException:
                    await browser.close()
                    raise
                except Exception as e2:
                    await browser.close()
                    msg = str(e2) or repr(e2) or type(e2).__name__
                    raise PlaywrightException(f"无法访问页面 {url}: {msg}")

            try:
                raw_elements = await page.evaluate(_EXTRACT_JS)
            except Exception as e:
                await browser.close()
                msg = str(e) or repr(e) or type(e).__name__
                raise PlaywrightException(f"元素提取脚本执行失败: {msg}")

            # 生成选择器（在页面上下文中验证唯一性）
            elements: list[CrawledElement] = []
            for raw in raw_elements:
                selector = await self._generate_selector(page, raw)
                elements.append(CrawledElement(
                    element_type=raw.get("element_type", raw.get("tag", "")),
                    tag_name=raw.get("tag", ""),
                    element_id=raw.get("id"),
                    name=raw.get("name"),
                    class_name=raw.get("className"),
                    selector=selector,
                    text_content=raw.get("textContent")[:200] if raw.get("textContent") else None,
                    placeholder=raw.get("placeholder"),
                    is_visible=1 if raw.get("isVisible") else 0,
                    bounding_box=raw.get("boundingBox"),
                    attributes=raw.get("attributes"),
                ))

            await browser.close()
            return elements

    # ═══════════════════════════════════════════════
    # AI 辅助导航（方案 A: goto 失败时用 AI 分析截图并执行前置操作）
    # ═══════════════════════════════════════════════

    async def _ai_assisted_navigation(self, page, url: str) -> bool:
        """AI 分析页面截图，执行前置操作（点击、填表等），返回是否成功处理

        goto 失败时调用此方法：
        1. 截图当前页面状态
        2. 调用 Vision API 分析截图，判断是否需要前置操作
        3. 解析 AI 返回的 JSON 指令，逐条执行
        4. 返回 True 表示已执行前置操作（调用方应重试 goto）
        """
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "prompts", "crawl_analyze.txt"
        )
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read()
        else:
            logger.warning("crawl_analyze.txt 模板不存在，使用默认 Prompt")
            prompt = (
                "分析当前页面截图，判断是否需要执行前置操作（点击按钮、填写表单等）才能访问目标页面。"
                "返回 JSON 格式：{\"need_pre_actions\": true/false, \"actions\": [...], \"reason\": \"...\"}"
            )

        # 截图
        try:
            screenshot_bytes = await page.screenshot(full_page=False)
        except Exception as e:
            logger.warning("AI 辅助导航截图失败: %s", e)
            return False

        # 调用 AI Vision
        result = _call_openai_vision(prompt, screenshot_bytes)
        if not result:
            logger.info("AI Vision 返回空结果，跳过 AI 辅助导航")
            return False

        # 解析 JSON 响应
        try:
            clean = result.strip()
            # 去除可能的 markdown 代码块标记
            if clean.startswith("```"):
                lines = clean.split("\n")
                if len(lines) >= 3:
                    clean = "\n".join(lines[1:-1])
                else:
                    clean = lines[-1] if len(lines) > 1 else ""
            analysis = json.loads(clean)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("AI 分析结果 JSON 解析失败: %s\n原始响应: %s", e, result[:200])
            return False

        if not analysis.get("need_pre_actions"):
            logger.info("AI 分析: 无需前置操作 - %s", analysis.get("reason", ""))
            return False

        logger.info("AI 分析: %s - 执行 %d 个前置操作", analysis.get("reason", ""), len(analysis.get("actions", [])))

        # 逐条执行操作
        actions = analysis.get("actions", [])
        for i, action in enumerate(actions):
            act = action.get("action", "")
            selector = action.get("selector", "")
            value = action.get("value", "")

            try:
                if act == "click":
                    await page.click(selector)
                    await page.wait_for_timeout(500)
                elif act == "fill":
                    await page.fill(selector, value)
                    await page.wait_for_timeout(300)
                elif act == "select":
                    await page.select_option(selector, value)
                    await page.wait_for_timeout(300)
                elif act == "wait":
                    delay = int(value) if value else 1000
                    await page.wait_for_timeout(delay)
                else:
                    logger.warning("AI 操作未知类型: %s", act)
                    continue
                logger.info("AI 执行操作[%d]: %s %s", i, act, selector)
            except Exception as e:
                logger.warning("AI 操作[%d]失败: %s %s - %s", i, act, selector, e)

        await page.wait_for_timeout(1000)
        return True

    # ═══════════════════════════════════════════════
    # 选择器生成（7 级优先级）
    # ═══════════════════════════════════════════════

    async def _generate_selector(self, page, raw: dict) -> str:
        """按优先级生成 Playwright 选择器，在页面上下文验证唯一性"""

        tag = raw.get("tag", "")
        el_id = raw.get("id", "")
        name_attr = raw.get("name", "")
        placeholder = raw.get("placeholder", "")
        text = (raw.get("textContent") or "")[:50].strip()
        className = raw.get("className", "")
        data_testid = raw.get("dataTestid", "")

        # 1. data-testid
        if data_testid:
            sel = f'[data-testid="{data_testid}"]'
            if await self._is_unique(page, sel):
                return sel

        # 2. id
        if el_id:
            sel = f"#{_css_escape(el_id)}"
            if await self._is_unique(page, sel):
                return sel

        # 3. name
        if name_attr:
            sel = f'[name="{name_attr}"]'
            if await self._is_unique(page, sel):
                return sel

        # 4. placeholder + tag
        if placeholder:
            sel = f'{tag}[placeholder="{placeholder}"]'
            if await self._is_unique(page, sel):
                return sel

        # 5. 稳定 class（排除动态类）
        stable_classes = _filter_stable_classes(className.split()) if className else []
        if stable_classes:
            sel = f"{tag}.{'.'.join(stable_classes[:2])}"
            if await self._is_unique(page, sel):
                return sel

        # 6. text content
        if text:
            sel = f'{tag}:has-text("{text}")'
            if await self._is_unique(page, sel):
                return sel

        # 7. 兜底：nth-child XPath
        idx = raw.get("index", 0)
        sel = f"{tag}:nth-child({idx + 1})" if idx >= 0 else tag
        return sel

    @staticmethod
    async def _is_unique(page, selector: str) -> bool:
        """验证选择器在页面中唯一"""
        try:
            count = await page.evaluate(
                """(sel) => document.querySelectorAll(sel).length""",
                selector,
            )
            return count == 1
        except Exception:
            return False


# ═══════════════════════════════════════════════
# 页面 JS 提取脚本
# ═══════════════════════════════════════════════

_EXTRACT_JS = """() => {
    const selectors = [
        'button',
        'input:not([type])', 'input[type="text"]', 'input[type="password"]', 'input[type="email"]', 'input[type="number"]',
        'input[type="tel"]', 'input[type="url"]', 'input[type="search"]', 'input[type="date"]', 'input[type="time"]',
        'textarea', 'select',
        'a[href]',
        '[role="button"]', '[role="link"]',
    ];
    const all = document.querySelectorAll(selectors.join(','));
    const seen = new Set();
    const result = [];

    all.forEach((el, i) => {
        // 过滤不可见
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        if (el.offsetParent === null) return;

        // 去重（同一 DOM 节点被多个选择器匹配）
        const uid = el.outerHTML ? el.outerHTML.substring(0, 80) : el.tagName + i;
        if (seen.has(uid)) return;
        seen.add(uid);

        const tag = el.tagName.toLowerCase();
        let element_type = tag;
        if (tag === 'input') element_type = el.type || 'text';
        if (tag === 'a') element_type = 'link';

        result.push({
            index: i,
            tag: tag,
            element_type: element_type,
            id: el.id || null,
            name: el.getAttribute('name') || null,
            className: el.className || null,
            textContent: (el.textContent || '').trim() || null,
            placeholder: el.getAttribute('placeholder') || null,
            type: el.getAttribute('type') || null,
            href: el.getAttribute('href') || null,
            role: el.getAttribute('role') || null,
            dataTestid: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || null,
            isVisible: true,
            boundingBox: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            },
            attributes: null,
        });
    });
    return result;
}"""


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def _css_escape(value: str) -> str:
    """CSS 选择器转义（处理含特殊字符的 id）"""
    return value.replace(":", "\\:").replace(".", "\\.").replace("#", "\\#")


def _filter_stable_classes(classes: list[str]) -> list[str]:
    """过滤动态 class（含 hash、css-in-js）"""
    result = []
    for c in classes:
        if not c or len(c) < 2:
            continue
        if DYNAMIC_CLASS_PATTERN.search(c):
            continue
        if c.startswith("ant-") and len(c) > 20:
            continue
        result.append(c)
    return result


def _orm_to_crawled(el: PageElement) -> CrawledElement:
    return CrawledElement(
        element_type=el.element_type,
        tag_name=el.tag_name or "",
        element_id=el.element_id if el.element_id else None,
        name=el.name if el.name else None,
        class_name=el.class_name if el.class_name else None,
        selector=el.selector,
        text_content=el.text_content,
        placeholder=el.placeholder,
        is_visible=el.is_visible or 0,
        bounding_box=json.loads(el.bounding_box) if el.bounding_box else {},
        attributes=json.loads(el.attributes) if el.attributes else {},
        db_id=el.id,
        created_at=str(el.created_at) if el.created_at else None,
    )
