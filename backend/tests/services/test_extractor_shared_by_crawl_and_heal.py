"""P1-2 验收：双路径统一 — ElementService 与 HealService._recrawl 调用同一共享实现

对应验收4：
- import 路径断言：两个 service 绑定的都是 element_extractor 的同一函数对象
- heal 的 _recrawl 行为测试：完整走共享 extract + 共享 generate_selector
- heal_service 内不得残留旧复制实现（_EXTRACT_JS / _DYNAMIC_CLASS / nth-child）
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import element_extractor, element_service, heal_service
from app.services.element_extractor import extract_elements, generate_selector, is_unique


class TestSharedImportPaths:
    """验收4a：ElementService / HealService 绑定的均为 element_extractor 同一实现"""

    def test_element_service_imports_shared_extract(self):
        assert element_service._extract_elements_shared is extract_elements

    def test_element_service_imports_shared_generate(self):
        assert element_service._generate_selector_shared is generate_selector

    def test_element_service_imports_shared_is_unique(self):
        assert element_service._is_unique_shared is is_unique

    def test_heal_service_imports_shared_extract(self):
        assert heal_service._extract_elements_shared is extract_elements

    def test_heal_service_imports_shared_generate(self):
        assert heal_service._generate_selector_shared is generate_selector

    def test_heal_service_imports_shared_is_unique(self):
        assert heal_service._is_unique_shared is is_unique

    def test_heal_service_has_no_copied_implementation(self):
        """heal_service 内不得残留旧复制实现（P1-2 冻结要求）"""
        source = inspect.getsource(heal_service)
        assert "_EXTRACT_JS" not in source
        assert "_DYNAMIC_CLASS" not in source
        assert "nth-child" not in source
        # 转发共享实现的调用必须在场
        assert "_extract_elements_shared" in source
        assert "_generate_selector_shared" in source

    def test_element_service_has_no_copied_implementation(self):
        source = inspect.getsource(element_service)
        assert "_EXTRACT_JS" not in source
        assert "nth-child" not in source
        assert "_extract_elements_shared" in source

    def test_shared_module_exports_required_api(self):
        """共享实现必须导出 extract_elements / generate_selector / is_unique"""
        assert callable(extract_elements)
        assert callable(generate_selector)
        assert callable(is_unique)


class TestSharedBehavior:
    """验收4b：行为层面两个 service 走同一实现、heal._recrawl 完整链路"""

    def _make_page(self):
        page = AsyncMock()
        locator = AsyncMock()
        locator.count = AsyncMock(return_value=1)
        page.locator = MagicMock(return_value=locator)
        return page

    @pytest.mark.asyncio
    async def test_both_services_produce_same_selector(self):
        """相同 page + 相同 raw → 两个 service 返回相同 selector（同一实现证明）"""
        from app.services.element_service import ElementService
        from app.services.heal_service import HealService

        page = self._make_page()
        raw = {
            "tag": "button", "id": "login-btn", "name": "", "placeholder": "",
            "textContent": "Login", "className": "", "dataTestid": "", "index": 0,
        }
        es_sel = await ElementService(db=None)._generate_selector(page, raw)
        hs_sel = await HealService(db=None)._generate_selector(page, raw)
        assert es_sel == hs_sel == "#login-btn"

    @pytest.mark.asyncio
    async def test_heal_recrawl_uses_shared_extract_and_selector(self):
        """heal._recrawl_elements 完整走共享 extract_elements + generate_selector"""
        from app.services.heal_service import HealService

        raw_list = [
            {
                "index": 0, "tag": "input", "element_type": "text",
                "id": "user-name", "name": None, "className": "form_input",
                "textContent": "", "placeholder": "Username", "type": "text",
                "href": None, "role": None, "dataTestid": None,
                "isVisible": True,
                "boundingBox": {"x": 0, "y": 0, "width": 100, "height": 30},
                "attributes": None,
            },
        ]
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=raw_list)
        locator = AsyncMock()
        locator.count = AsyncMock(return_value=1)
        page.locator = MagicMock(return_value=locator)
        page.evaluate_handle = AsyncMock(return_value=AsyncMock())

        elements = await HealService(db=None)._recrawl_elements(page)

        assert len(elements) == 1
        assert elements[0]["selector"] == "#user-name"
        # 关键：_recrawl 内部调用的就是共享 extract（同一函数对象）
        assert heal_service._extract_elements_shared is extract_elements

    def test_element_service_extract_delegates_to_shared(self):
        """element_service._extract_elements 内部调用共享 extract_elements（源码级断言）"""
        source = inspect.getsource(element_service.ElementService._extract_elements)
        assert "_extract_elements_shared(page)" in source

    @pytest.mark.asyncio
    async def test_element_extractor_module_is_thread_local_singleton(self):
        """模块级接口持有线程本地实例（身份上下文按线程隔离）"""
        e1 = element_extractor._extractor()
        e2 = element_extractor._extractor()
        assert e1 is e2  # 同一线程内单例
