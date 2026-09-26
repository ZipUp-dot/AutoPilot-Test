"""页面元素提取 + 选择器生成 — Web 抓取与 Heal Recrawl 的唯一共享实现

P1-2 变更（2026-09-26）：
- 白名单补全：submit/checkbox/radio/file/range/color/datetime-local/select/textarea/
  button/summary/contenteditable 及 role 系列真实可交互控件（只扩覆盖，不改过滤哲学）。
- 唯一性检查：page.locator(sel).count()（Playwright-native），使 :has-text() 等
  Playwright 专属语法真正参与唯一性判定（不再用 document.querySelectorAll 验证）。
- 索引兜底：基于目标元素实际父子层级逐级 nth-of-type 向上扩展至可唯一定位祖先，
  经 page.locator(sel).count()==1 + 原始元素 identity 双重验证；count!=1 继续向上，
  层级耗尽退化为 XPath。
- 身份上下文：extract_elements() 内部持有原始 DOM 元素句柄（_identities），
  generate_selector() 兜底基于该原始元素做 identity comparison（禁止退化为只验
  count==1）；持久化元素数据保持 JSON 可序列化，ElementHandle 仅存于内存、禁止入库。
- 本模块是 ElementService 与 HealService._recrawl 的【唯一】实现来源；两处均调用
  模块级 extract_elements / generate_selector / is_unique（线程本地实例持有身份上下文）。
"""

import json
import logging
import re
import threading
from typing import Any, Optional

logger = logging.getLogger("autopilot.extractor")


# ═══════════════════════════════════════════════
# 提取白名单（真实可交互控件）
# ═══════════════════════════════════════════════

_SELECTOR_PATTERNS = [
    "button",
    # input：未指定 type + 文本类 + 选择类 + 提交/重置换 + 文件 + 状态类
    "input:not([type])",
    "input[type='text']", "input[type='password']", "input[type='email']",
    "input[type='number']", "input[type='tel']", "input[type='url']",
    "input[type='search']", "input[type='date']", "input[type='time']",
    "input[type='datetime-local']", "input[type='month']", "input[type='week']",
    "input[type='submit']", "input[type='reset']", "input[type='checkbox']",
    "input[type='radio']", "input[type='file']", "input[type='range']",
    "input[type='color']",
    "textarea", "select", "summary",
    "[contenteditable='true']",
    "a[href]",
    # role 系列可交互控件
    "[role='button']", "[role='link']", "[role='checkbox']", "[role='radio']",
    "[role='combobox']", "[role='textbox']", "[role='searchbox']",
    "[role='switch']", "[role='slider']", "[role='spinbutton']",
    "[role='tab']", "[role='menuitem']",
]

# 提取脚本：返回 JSON 可序列化元素描述；index 为 querySelectorAll 结果中的序号
# （document order，确定性；identity 复取依赖同一序号语义）
_EXTRACT_JS = f"""() => {{
    const selectors = {json.dumps(_SELECTOR_PATTERNS, ensure_ascii=False)};
    const all = document.querySelectorAll(selectors.join(','));
    const seen = new Set();
    const result = [];

    all.forEach((el, i) => {{
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        if (el.offsetParent === null) return;

        const uid = el.outerHTML ? el.outerHTML.substring(0, 80) : el.tagName + i;
        if (seen.has(uid)) return;
        seen.add(uid);

        const tag = el.tagName.toLowerCase();
        let element_type = tag;
        if (tag === 'input') element_type = el.type || 'text';
        if (tag === 'a') element_type = 'link';

        result.push({{
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
            boundingBox: {{
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            }},
            attributes: null,
        }});
    }});
    return result;
}}"""

# 按序号复取原始 DOM 节点（Playwright 将 DOM 节点包装为 ElementHandle）
_GET_BY_INDEX_JS = f"""(idx) => {{
    const nodes = document.querySelectorAll({json.dumps(_SELECTOR_PATTERNS, ensure_ascii=False)}.join(','));
    return nodes[idx] || null;
}}"""

# 在原始元素上计算逐级父子层级（html 至目标），每级记录 tag + nth-of-type 序号
_ANCESTOR_PATH_JS = """(el) => {
    const path = [];
    let node = el;
    while (node && node.nodeType === 1 && node.parentElement) {
        let parent = node.parentElement;
        let tag = node.tagName.toLowerCase();
        let idx = 1;
        for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
            if (sib.tagName === node.tagName) idx++;
        }
        path.unshift({ tag: tag, idx: idx });
        node = parent;
    }
    return path;
}"""

# 纯 CSS 层级路径的 identity 验证：解析回的 DOM 元素必须与原始目标是同一个
# （仅用于 nth-of-type 层级兜底，不用于 :has-text() 等 Playwright 专属语法）
_IDENTITY_JS = """(args) => {
    const el = args[0];
    const sel = args[1];
    const n = document.querySelectorAll(sel);
    return n.length === 1 && n[0] === el;
}"""


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

_DYNAMIC_CLASS_PATTERN = re.compile(
    r'(css-[a-z0-9]+|_[a-zA-Z0-9]{6,}|[a-z]+-[a-f0-9]{6,}|sc-[a-zA-Z]+$)'
)


def _css_escape(value: str) -> str:
    """CSS 选择器转义（处理含特殊字符的 id）"""
    return value.replace(":", "\\:").replace(".", "\\.").replace("#", "\\#")


def _filter_stable_classes(classes: list[str]) -> list[str]:
    """过滤动态 class（含 hash、css-in-js）"""
    result = []
    for c in classes:
        if not c or len(c) < 2:
            continue
        if _DYNAMIC_CLASS_PATTERN.search(c):
            continue
        if c.startswith("ant-") and len(c) > 20:
            continue
        result.append(c)
    return result


def _degrade_xpath(raw: dict) -> str:
    """层级耗尽 / 无身份句柄时的 XPath 兜底（持久化字符串，可被 locator 解析）"""
    tag = raw.get("tag", "*")
    for attr in ("id", "name", "placeholder", "type"):
        val = raw.get(attr)
        if val:
            safe = str(val).replace("'", "\\'")
            return f"xpath=//{tag}[@{attr}='{safe}']"
    return f"xpath=//{tag}"


# ═══════════════════════════════════════════════
# ElementExtractor — 持有身份上下文的共享实现
# ═══════════════════════════════════════════════

class ElementExtractor:
    """页面元素提取 + 选择器生成（唯一实现来源）

    - extract_elements(page) 内部保留原始元素句柄（_identities: index → ElementHandle）
    - generate_selector(page, raw) 兜底基于该原始元素做 identity 验证
    - is_unique(page, sel) 用 page.locator(sel).count()（Playwright-native）
    """

    def __init__(self) -> None:
        self._identities: dict[int, Optional[Any]] = {}

    # ── 提取 ──

    async def extract_elements(self, page) -> list[dict]:
        """提取页面可交互元素：返回 JSON 可序列化 dict 列表，同时内部持有元素句柄"""
        raw_list = await page.evaluate(_EXTRACT_JS) or []
        self._identities = {}
        for raw in raw_list:
            idx = raw.get("index")
            if isinstance(idx, int) and idx >= 0:
                try:
                    self._identities[idx] = await page.evaluate_handle(_GET_BY_INDEX_JS, idx)
                except Exception:
                    self._identities[idx] = None
        return raw_list

    # ── 选择器生成（7 级优先级 + 层级兜底）──

    async def generate_selector(self, page, raw: dict) -> str:
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
            if await self.is_unique(page, sel):
                return sel

        # 2. id
        if el_id:
            sel = f"#{_css_escape(el_id)}"
            if await self.is_unique(page, sel):
                return sel

        # 3. name
        if name_attr:
            sel = f'[name="{name_attr}"]'
            if await self.is_unique(page, sel):
                return sel

        # 4. placeholder + tag
        if placeholder:
            sel = f'{tag}[placeholder="{placeholder}"]'
            if await self.is_unique(page, sel):
                return sel

        # 5. 稳定 class（排除动态类）
        stable_classes = _filter_stable_classes(className.split()) if className else []
        if stable_classes:
            sel = f"{tag}.{'.'.join(stable_classes[:2])}"
            if await self.is_unique(page, sel):
                return sel

        # 6. text content（Playwright 专属 :has-text()，经 locator.count() 参与判定）
        if text:
            sel = f'{tag}:has-text("{text}")'
            if await self.is_unique(page, sel):
                return sel

        # 7. 层级兜底（nth-of-type 逐级向上 + identity 验证，禁止全局序号 + nth-child）
        return await self._hierarchy_fallback(page, raw)

    # ── 唯一性检查（Playwright-native）──

    @staticmethod
    async def is_unique(page, selector: str) -> bool:
        """验证选择器在页面中唯一（page.locator().count()）

        :has-text() 等 Playwright 专属语法经 locator 引擎解析；
        禁止回退 document.querySelectorAll（会把专属语法当 CSS 解析而抛异常）。
        """
        try:
            count = await page.locator(selector).count()
            return count == 1
        except Exception:
            return False

    # ── 层级兜底 ──

    async def _hierarchy_fallback(self, page, raw: dict) -> str:
        """基于目标元素实际父子层级生成 nth-of-type 完整路径

        从目标逐级向上扩展（最短层级优先），每级满足：
          page.locator(sel).count()==1 且 解析回的 DOM 元素与原始目标【同一元素】；
        count!=1 或元素不匹配 → 继续向上扩展；层级耗尽退化为 XPath。
        """
        idx = raw.get("index", -1)
        identity = await self._resolve_identity(page, idx)
        path = await self._ancestor_path(identity)
        if not path:
            return _degrade_xpath(raw)

        for depth in range(1, len(path) + 1):
            sel = " > ".join(f"{p['tag']}:nth-of-type({p['idx']})" for p in path[-depth:])
            if await self.is_unique(page, sel):
                if identity is None:
                    # 无身份句柄：无法验证 identity，禁止只验 count==1 → 退化为 XPath
                    return _degrade_xpath(raw)
                if await self._matches_identity(page, identity, sel):
                    return sel
                # count==1 但是另一个元素 → 继续向上扩展层级
        return _degrade_xpath(raw)

    async def _resolve_identity(self, page, idx):
        """优先使用 extract_elements 保留的句柄，缺省时按序号惰性复取"""
        if idx is None or idx < 0:
            return None
        ident = self._identities.get(idx)
        if ident is not None:
            return ident
        try:
            return await page.evaluate_handle(_GET_BY_INDEX_JS, idx)
        except Exception:
            return None

    async def _ancestor_path(self, identity) -> list:
        if identity is None:
            return []
        try:
            path = await identity.evaluate(_ANCESTOR_PATH_JS)
        except Exception:
            return []
        if not isinstance(path, list) or not path:
            return []
        return path

    async def _matches_identity(self, page, identity, selector: str) -> bool:
        """验证选择器解析回的 DOM 元素与原始目标是同一个（identity comparison）"""
        try:
            same = await page.evaluate(_IDENTITY_JS, [identity, selector])
            return bool(same)
        except Exception:
            return False


# ═══════════════════════════════════════════════
# 模块级接口（线程本地实例持有身份上下文）
# ═══════════════════════════════════════════════

_local = threading.local()


def _extractor() -> ElementExtractor:
    ext = getattr(_local, "extractor", None)
    if ext is None:
        ext = _local.extractor = ElementExtractor()
    return ext


async def extract_elements(page) -> list[dict]:
    return await _extractor().extract_elements(page)


async def generate_selector(page, raw: dict) -> str:
    return await _extractor().generate_selector(page, raw)


async def is_unique(page, selector: str) -> bool:
    return await ElementExtractor.is_unique(page, selector)
