"""P1-2 验收：selector 层级兜底 — nth-of-type + 元素 identity 验证

对应验收3：
- fallback：locator.count()==1 且 resolved element 与原始目标元素【同一元素】
- 多容器同标签嵌套用例：nth-of-type 非全局唯一、与"恰好唯一但是另一个元素"

禁止事项校验：
- 禁止 fallback 验证退化为只验 count==1（必须元素 identity）
- 禁止把 locator.nth() 运行时 API 链存进 selector 字段（层级路径是持久化字符串）
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.element_extractor import ElementExtractor


def _make_page(counts, identity_path=None, identity_js_results=None):
    """构造层级兜底测试页

    counts: 每次 page.locator().count() 依次返回值
    identity_path: evaluate_handle 复取的原始元素句柄返回的祖先路径（html→目标）
    identity_js_results: _IDENTITY_JS 依次返回值（count==1 时的 identity comparison）
    """
    page = AsyncMock()
    counts = list(counts)

    def _locator(_sel):
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=counts.pop(0))
        return loc

    page.locator = MagicMock(side_effect=_locator)

    if identity_path is not None:
        identity = AsyncMock()
        identity.evaluate = AsyncMock(return_value=identity_path)
        page.evaluate_handle = AsyncMock(return_value=identity)
    else:
        page.evaluate_handle = AsyncMock(return_value=None)

    if identity_js_results is not None:
        results = list(identity_js_results)
        page.evaluate = AsyncMock(side_effect=lambda *a, **k: results.pop(0))
    return page


def _raw_div(index=0):
    return {
        "tag": "div", "index": index, "dataTestid": "", "id": "",
        "name": "", "placeholder": "", "textContent": "", "className": "",
    }


class TestHierarchyFallbackIdentity:
    """验收3：层级兜底必须验证解析回元素与原始目标元素同一元素"""

    @pytest.mark.asyncio
    async def test_nth_of_type_expands_upward_when_not_globally_unique(self):
        """场景A：两个容器各有一个 div → div:nth-of-type(1) 全局不唯一（count=2）
        → 向上扩展一层（容器限定）→ count==1 且 identity 匹配 → 返回层级路径
        """
        page = _make_page(
            counts=[2, 1],  # depth1=2（非全局唯一）→ depth2=1（唯一）
            identity_path=[
                {"tag": "html", "idx": 1}, {"tag": "body", "idx": 1},
                {"tag": "div", "idx": 1}, {"tag": "div", "idx": 1},
            ],
            identity_js_results=[True],
        )
        ext = ElementExtractor()
        sel = await ext.generate_selector(page, _raw_div(index=0))
        assert sel == "div:nth-of-type(1) > div:nth-of-type(1)"

    @pytest.mark.asyncio
    async def test_count_1_but_other_element_rejected_and_expands(self):
        """场景B："恰好唯一但是另一个元素"：depth1 的 div:nth-of-type(5) count==1，
        但 identity 验证失败（解析回的是另一个元素）→ 继续向上扩展 → depth2 才返回
        """
        page = _make_page(
            counts=[1, 1],
            identity_path=[
                {"tag": "html", "idx": 1}, {"tag": "body", "idx": 1},
                {"tag": "div", "idx": 1}, {"tag": "div", "idx": 5},
            ],
            identity_js_results=[False, True],
        )
        ext = ElementExtractor()
        sel = await ext.generate_selector(page, _raw_div(index=0))
        assert sel == "div:nth-of-type(1) > div:nth-of-type(5)"
        # 确实发生过"count==1 但 identity 拒绝"（_IDENTITY_JS 被调用两次）
        assert page.evaluate.call_count == 2

    @pytest.mark.asyncio
    async def test_no_identity_handle_degrades_to_xpath(self):
        """场景C：无身份句柄 → 即使 count==1 也禁止返回（不退化只验 count）→ XPath"""
        page = _make_page(counts=[1], identity_path=None)
        ext = ElementExtractor()
        sel = await ext.generate_selector(page, _raw_div(index=0))
        assert sel == "xpath=//div"

    @pytest.mark.asyncio
    async def test_hierarchy_exhausted_degrades_to_xpath(self):
        """场景D：identity 始终拒绝 → 层级耗尽 → XPath"""
        page = _make_page(
            counts=[1, 0, 0, 0],
            identity_path=[
                {"tag": "html", "idx": 1}, {"tag": "body", "idx": 1},
                {"tag": "div", "idx": 1}, {"tag": "div", "idx": 1},
            ],
            identity_js_results=[False],
        )
        ext = ElementExtractor()
        sel = await ext.generate_selector(page, _raw_div(index=0))
        assert sel.startswith("xpath=")

    @pytest.mark.asyncio
    async def test_fallback_selector_never_uses_locator_nth(self):
        """兜底产出的 selector 是持久化字符串（nth-of-type 层级），不含 locator.nth()"""
        page = _make_page(
            counts=[2, 1],
            identity_path=[
                {"tag": "html", "idx": 1}, {"tag": "body", "idx": 1},
                {"tag": "div", "idx": 1}, {"tag": "div", "idx": 3},
            ],
            identity_js_results=[True],
        )
        ext = ElementExtractor()
        sel = await ext.generate_selector(page, _raw_div(index=2))
        assert "nth(" not in sel
        assert "nth-of-type" in sel


class TestIdentityContext:
    """验收5（identity 上下文）：extract_elements 内部保留原始元素句柄，持久化数据保持 JSON 可序列化"""

    @pytest.mark.asyncio
    async def test_extract_elements_keeps_identity_handle_in_memory(self):
        raw_list = [{
            "index": 0, "tag": "button", "element_type": "button",
            "id": "", "name": "", "placeholder": "", "textContent": "Go",
            "className": "", "dataTestid": "", "isVisible": True,
            "boundingBox": {"x": 0, "y": 0, "width": 10, "height": 10},
            "attributes": None,
        }]
        identity = AsyncMock()
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=raw_list)
        page.evaluate_handle = AsyncMock(return_value=identity)

        ext = ElementExtractor()
        elements = await ext.extract_elements(page)
        assert len(elements) == 1
        # 原始 DOM 元素句柄保留在提取器内部（仅内存，不随 raw 返回）
        assert ext._identities[0] is identity

        # 对外返回的持久化元素数据保持 JSON 可序列化（ElementHandle 不入库）
        assert json.dumps(elements[0])
        assert "ElementHandle" not in json.dumps(elements[0])
