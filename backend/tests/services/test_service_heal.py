"""自愈服务测试 — pytest 风格，覆盖错误分类、代码提取、校验、AI 调用、DB 记录"""

import json
from unittest.mock import MagicMock, AsyncMock

import pytest

from app.services.heal_service import (
    HealService,
    _css_escape,
    _filter_stable_classes,
    _parse_android_elements,
    _format_android_elements,
)
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.models.heal_record import HealRecord


# ── Fixtures ──

@pytest.fixture
def heal_svc():
    """无需 DB 的 HealService（用于静态方法/纯逻辑测试）"""
    return HealService(db=None)


@pytest.fixture
def heal_svc_db(db_session):
    """带 DB session 的 HealService"""
    return HealService(db_session)


# ═══════════════════════════════════════════════
# _classify_error
# ═══════════════════════════════════════════════

class TestClassifyError:
    """_classify_error() 错误分类"""

    def test_timeout(self, heal_svc):
        assert heal_svc._classify_error("Timeout 30000ms exceeded") == "TimeoutError"

    def test_element_not_found_locator(self, heal_svc):
        assert heal_svc._classify_error("Cannot resolve locator: #missing") == "ElementNotFoundError"

    def test_element_not_found_element(self, heal_svc):
        assert heal_svc._classify_error("Element not found: .btn-submit") == "ElementNotFoundError"

    def test_assertion_failed(self, heal_svc):
        assert heal_svc._classify_error("Assertion failed: expected visible") == "AssertionError"

    def test_assertion_expect(self, heal_svc):
        assert heal_svc._classify_error("expect(...).to_be_visible failed") == "AssertionError"

    def test_navigation_error(self, heal_svc):
        assert heal_svc._classify_error("net::ERR_CONNECTION_REFUSED") == "NavigationError"

    def test_unknown(self, heal_svc):
        assert heal_svc._classify_error("some random error") == "UnknownError"

    def test_appium_stale_element(self, heal_svc):
        """Appium StaleElementReferenceException → StaleElementError"""
        assert heal_svc._classify_error(
            "StaleElementReferenceException: element is not attached to the page document"
        ) == "StaleElementError"

    def test_appium_stale_element_short(self, heal_svc):
        """Appium 'stale element' 短描述 → StaleElementError"""
        assert heal_svc._classify_error("stale element reference: element is detached") == "StaleElementError"

    def test_appium_no_such_element(self, heal_svc):
        """Appium NoSuchElementException → ElementNotFoundError"""
        assert heal_svc._classify_error(
            "NoSuchElementException: An element could not be located on the page"
        ) == "ElementNotFoundError"

    def test_appium_timeout_exception(self, heal_svc):
        """Appium TimeoutException → TimeoutError"""
        assert heal_svc._classify_error(
            "TimeoutException: Timed out after 10000ms waiting for element"
        ) == "TimeoutError"

    def test_appium_webdriver_exception(self, heal_svc):
        """Appium WebDriverException → DriverError"""
        assert heal_svc._classify_error(
            "WebDriverException: Message: An unknown server-side error occurred"
        ) == "DriverError"


# ═══════════════════════════════════════════════
# _extract_code
# ═══════════════════════════════════════════════

class TestExtractCode:
    """_extract_code() 代码提取"""

    def test_markdown_python(self):
        code = "```python\nasync def run_test(page): pass\n```"
        result = HealService._extract_code(code)
        assert result == "async def run_test(page): pass"

    def test_markdown_no_lang(self):
        code = "```\nasync def run_test(page): pass\n```"
        result = HealService._extract_code(code)
        assert result == "async def run_test(page): pass"

    def test_plain_code(self):
        code = "async def run_test(page): pass"
        result = HealService._extract_code(code)
        assert result == "async def run_test(page): pass"

    def test_only_opening_backticks(self):
        """只有开头 ``` 没有结尾 ``` 的情况"""
        code = "```python\nasync def run_test(page): pass"
        result = HealService._extract_code(code)
        assert result == "async def run_test(page): pass"

    def test_markdown_multiline(self):
        code = (
            "```python\n"
            "from playwright.async_api import Page\n\n"
            "async def run_test(page: Page):\n"
            '    return {"success": True}\n'
            "```"
        )
        result = HealService._extract_code(code)
        assert "async def run_test" in result
        assert result.startswith("from playwright")


# ═══════════════════════════════════════════════
# _validate_healed
# ═══════════════════════════════════════════════

class TestValidateHealed:
    """_validate_healed() 代码校验"""

    VALID_CODE = (
        "from playwright.async_api import Page, expect\n"
        "import asyncio\n"
        "from datetime import datetime\n\n"
        "async def run_test(page: Page) -> dict:\n"
        "    steps_result = []\n"
        "    start_time = datetime.now()\n"
        "    try:\n"
        '        await page.goto("https://example.com")\n'
        '        await page.locator("h1").click()\n'
        '        await expect(page.locator("h1")).to_contain_text("Example")\n'
        '        steps_result.append({"step": 1, "status": "passed"})\n'
        "    except Exception as e:\n"
        '        return {"success": False, "message": str(e), "steps": steps_result}\n'
        '    return {"success": True, "message": "ok", "steps": steps_result}\n'
    )

    def test_valid_code_returns_none(self):
        error = HealService._validate_healed(self.VALID_CODE)
        assert error is None

    def test_dangerous_import_os(self):
        code = "import os\nasync def run_test(page):\n    os.system('rm -rf /')\n    return {}"
        error = HealService._validate_healed(code)
        assert error is not None
        assert "禁止导入模块" in error

    def test_dangerous_import_subprocess(self):
        code = "import subprocess\nasync def run_test(page):\n    subprocess.run('ls')\n    return {}"
        error = HealService._validate_healed(code)
        assert error is not None
        assert "禁止导入模块" in error

    def test_missing_run_test(self):
        code = "print('hello world')"
        error = HealService._validate_healed(code)
        assert error is not None
        assert "缺少" in error or "run_test" in error

    def test_syntax_error(self):
        code = "async def run_test(page):\n    return {{{"
        error = HealService._validate_healed(code)
        assert error is not None
        assert "语法错误" in error


# ═══════════════════════════════════════════════
# _build_heal_prompt
# ═══════════════════════════════════════════════

class TestBuildHealPrompt:
    """_build_heal_prompt() Prompt 构建"""

    def test_all_placeholders_filled(self, heal_svc):
        from unittest.mock import MagicMock

        step = MagicMock()
        step.step_index = 3
        step.action = "click"
        step.target_selector = "#submit-btn"

        error_ctx = {
            "error_message": "Timeout 30000ms exceeded",
            "dom_snapshot": "<html>...</html>",
            "screenshot_before": "/path/to/before.png",
            "screenshot_after": "/path/to/after.png",
            "elements_list": "[button] tag=button selector=#btn",
        }
        original_code = "async def run_test(page): pass"

        prompt = heal_svc._build_heal_prompt(error_ctx, original_code, step)

        # 所有占位符应被填充
        assert original_code in prompt
        assert "3" in prompt  # step_index
        assert "click" in prompt  # action
        assert "#submit-btn" in prompt  # target
        assert "Timeout 30000ms exceeded" in prompt
        assert "<html>...</html>" in prompt
        assert "/path/to/before.png" in prompt
        assert "/path/to/after.png" in prompt
        assert "[button]" in prompt
        # 确认没有残留的 {xxx} 占位符
        assert "{original_code}" not in prompt
        assert "{failed_step_index}" not in prompt
        assert "{failed_action}" not in prompt
        assert "{failed_target}" not in prompt
        assert "{error_message}" not in prompt
        assert "{dom_snapshot}" not in prompt
        assert "{screenshot_before}" not in prompt
        assert "{screenshot_after}" not in prompt
        assert "{elements_list}" not in prompt


# ═══════════════════════════════════════════════
# _call_heal_ai
# ═══════════════════════════════════════════════

class TestCallHealAI:
    """_call_heal_ai() AI 调用"""

    def test_no_api_key_returns_mock_response(self, heal_svc):
        """未配置 OPENAI_API_KEY → 返回 mock 自愈响应"""
        # conftest 已设置 OPENAI_API_KEY=""，所以走 mock 分支
        result = heal_svc._call_heal_ai("fix this code")
        assert "async def run_test" in result
        assert "Mock" in result or "[自愈]" in result


# ═══════════════════════════════════════════════
# _save_heal_record
# ═══════════════════════════════════════════════

class TestSaveHealRecord:
    """_save_heal_record() 保存自愈记录"""

    def test_creates_heal_record_with_all_fields(self, db_session, sample_project, sample_test_case, sample_execution):
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        record = svc._save_heal_record(
            step_id=step.id,
            original_code="await page.click('#btn')",
            error_ctx={"error": "timeout", "type": "TimeoutError"},
            healed_code="await page.click('#new-btn')",
            prompt="fix this please",
            retry_count=1,
        )

        assert record.id > 0
        assert record.execution_step_id == step.id
        assert record.original_code == "await page.click('#btn')"
        assert record.retry_status == "retrying"
        assert record.retry_count == 1
        assert record.healed_code == "await page.click('#new-btn')"
        # error_context 是 JSON 字符串
        ctx = json.loads(record.error_context)
        assert ctx["error"] == "timeout"


# ═══════════════════════════════════════════════
# _update_heal_record
# ═══════════════════════════════════════════════

class TestUpdateHealRecord:
    """_update_heal_record() 更新自愈记录状态"""

    def test_updates_retry_status(self, db_session, sample_project, sample_test_case, sample_execution):
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        record = svc._save_heal_record(
            step_id=step.id,
            original_code="await page.click('#btn')",
            error_ctx={"error": "timeout"},
            healed_code="await page.click('#new-btn')",
            prompt="fix this",
            retry_count=2,
        )
        assert record.retry_status == "retrying"

        # 更新为 success
        svc._update_heal_record(record.id, "success")
        db_session.refresh(record)
        assert record.retry_status == "success"

        # 更新为 failed
        svc._update_heal_record(record.id, "failed")
        db_session.refresh(record)
        assert record.retry_status == "failed"


# ═══════════════════════════════════════════════
# _insert_healed_code
# ═══════════════════════════════════════════════

class TestInsertHealedCode:
    """_insert_healed_code() 插入修复代码"""

    def test_creates_generated_code_with_is_healed(self, db_session, sample_project, sample_test_case):
        svc = HealService(db_session)
        healed_code = (
            "async def run_test(page):\n"
            '    return {"success": True, "steps": []}\n'
        )
        svc._insert_healed_code(
            case_id=sample_test_case.id,
            healed_code=healed_code,
            prompt="fix the timeout issue",
        )

        # 验证 GeneratedCode 被创建
        gen = (
            db_session.query(GeneratedCode)
            .filter(GeneratedCode.case_id == 1, GeneratedCode.is_healed == 1)
            .first()
        )
        assert gen is not None
        assert gen.code_content == healed_code
        assert gen.is_healed == 1
        assert gen.is_valid == 1
        assert gen.generation_prompt == "fix the timeout issue"


# ═══════════════════════════════════════════════
# _capture_failure_context
# ═══════════════════════════════════════════════

class TestCaptureFailureContext:
    """_capture_failure_context() 失败上下文捕获"""

    @pytest.mark.asyncio
    async def test_captures_basic_context(self, heal_svc_db):
        """捕获基本上下文：action/target/error_type/error_message"""
        page = AsyncMock()
        page.content.return_value = "<html><body>test</body></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=2,
            action="click", target_selector="#btn",
            input_value="", error_message="Timeout 30000ms exceeded",
            screenshot_before="before.png", screenshot_after="after.png",
        )

        ctx = await heal_svc_db._capture_failure_context(step, page)

        assert ctx["action"] == "click"
        assert ctx["target"] == "#btn"
        assert ctx["error_type"] == "TimeoutError"
        assert "Timeout" in ctx["error_message"]
        assert ctx["screenshot_before"] == "before.png"
        assert ctx["screenshot_after"] == "after.png"

    @pytest.mark.asyncio
    async def test_captures_dom_snapshot(self, heal_svc_db):
        """捕获 DOM 快照"""
        page = AsyncMock()
        page.content.return_value = "<html><body><h1>Hello</h1></body></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="navigate", error_message="Some error",
        )

        ctx = await heal_svc_db._capture_failure_context(step, page)
        assert "<h1>Hello</h1>" in ctx["dom_snapshot"]

    @pytest.mark.asyncio
    async def test_handles_dom_failure_gracefully(self, heal_svc_db):
        """DOM 获取失败时优雅降级"""
        page = AsyncMock()
        page.content.side_effect = Exception("Connection lost")
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", error_message="error",
        )

        ctx = await heal_svc_db._capture_failure_context(step, page)
        assert "无法获取 DOM 快照" in ctx["dom_snapshot"]

    @pytest.mark.asyncio
    async def test_handles_elements_failure_gracefully(self, heal_svc_db):
        """元素重抓失败时优雅降级"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.side_effect = Exception("JS error")

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", error_message="error",
        )

        ctx = await heal_svc_db._capture_failure_context(step, page)
        assert "无法获取页面元素" in ctx["elements_list"]

    @pytest.mark.asyncio
    async def test_truncates_error_message(self, heal_svc_db):
        """错误消息截断至 500 字符"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        long_error = "x" * 1000
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", error_message=long_error,
        )

        ctx = await heal_svc_db._capture_failure_context(step, page)
        assert len(ctx["error_message"]) <= 500


# ═══════════════════════════════════════════════
# _get_original_code
# ═══════════════════════════════════════════════

class TestGetOriginalCode:
    """_get_original_code() 获取原始代码"""

    def test_returns_code_when_exists(self, heal_svc_db, sample_generated_code):
        """有代码记录时返回 code_content"""
        code = heal_svc_db._get_original_code(sample_generated_code.case_id)
        assert "async def run_test" in code
        assert "success" in code

    def test_returns_empty_when_no_code(self, heal_svc_db):
        """无代码记录时返回空字符串"""
        code = heal_svc_db._get_original_code(99999)
        assert code == ""

    def test_returns_empty_when_code_invalid(self, db_session, sample_test_case):
        """is_valid=0 的代码不会被返回"""
        gen = GeneratedCode(
            case_id=sample_test_case.id,
            code_content="invalid code",
            is_valid=0,
        )
        db_session.add(gen)
        db_session.commit()

        svc = HealService(db_session)
        code = svc._get_original_code(sample_test_case.id)
        assert code == ""


# ═══════════════════════════════════════════════
# _recrawl_elements
# ═══════════════════════════════════════════════

class TestRecrawlElements:
    """_recrawl_elements() 页面元素重抓"""

    @pytest.mark.asyncio
    async def test_returns_elements_list(self, heal_svc, mocker):
        """返回带选择器的元素列表"""
        page = AsyncMock()
        raw_elements = [
            {
                "index": 0, "tag": "button", "element_type": "button",
                "id": "submit-btn", "name": None, "className": "btn primary",
                "textContent": "Submit", "placeholder": None,
                "type": None, "href": None, "role": None, "dataTestid": None,
                "isVisible": True, "boundingBox": {"x": 0, "y": 0, "width": 100, "height": 40},
            },
        ]
        page.evaluate.return_value = raw_elements
        # Mock _generate_selector 和 _is_unique
        mocker.patch.object(heal_svc, "_generate_selector", return_value="#submit-btn")

        elements = await heal_svc._recrawl_elements(page)

        assert len(elements) == 1
        assert elements[0]["tag"] == "button"
        assert elements[0]["text"] == "Submit"
        assert elements[0]["selector"] == "#submit-btn"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_elements(self, heal_svc):
        """页面无元素时返回空列表"""
        page = AsyncMock()
        page.evaluate.return_value = []

        elements = await heal_svc._recrawl_elements(page)
        assert elements == []


# ═══════════════════════════════════════════════
# _generate_selector
# ═══════════════════════════════════════════════

class TestGenerateSelector:
    """_generate_selector() 选择器生成（按优先级）"""

    @pytest.mark.asyncio
    async def test_selector_by_data_testid(self, heal_svc):
        """优先使用 data-testid"""
        page = AsyncMock()
        page.evaluate.return_value = 1  # is_unique → True

        raw = {
            "tag": "button", "id": "btn1", "name": "submit",
            "placeholder": "", "textContent": "Click", "className": "",
            "dataTestid": "my-button",
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert 'data-testid="my-button"' in sel

    @pytest.mark.asyncio
    async def test_selector_by_id_when_no_testid(self, heal_svc):
        """无 data-testid 时使用 id"""
        page = AsyncMock()
        page.evaluate.return_value = 1  # is_unique → True

        raw = {
            "tag": "button", "id": "submit-btn", "name": "",
            "placeholder": "", "textContent": "", "className": "",
            "dataTestid": "",
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert sel == "#submit-btn"

    @pytest.mark.asyncio
    async def test_selector_by_name(self, heal_svc):
        """无 id 时使用 name"""
        page = AsyncMock()
        page.evaluate.return_value = 1

        raw = {
            "tag": "input", "id": "", "name": "username",
            "placeholder": "", "textContent": "", "className": "",
            "dataTestid": "",
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert 'name="username"' in sel

    @pytest.mark.asyncio
    async def test_selector_by_placeholder(self, heal_svc):
        """无 name 时使用 placeholder"""
        page = AsyncMock()
        page.evaluate.return_value = 1

        raw = {
            "tag": "input", "id": "", "name": "",
            "placeholder": "Enter email", "textContent": "", "className": "",
            "dataTestid": "",
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert 'placeholder="Enter email"' in sel

    @pytest.mark.asyncio
    async def test_selector_by_stable_class(self, heal_svc):
        """无 placeholder 时使用稳定 class"""
        page = AsyncMock()
        page.evaluate.return_value = 1

        raw = {
            "tag": "button", "id": "", "name": "",
            "placeholder": "", "textContent": "", "className": "btn-primary large",
            "dataTestid": "",
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert "btn-primary" in sel

    @pytest.mark.asyncio
    async def test_selector_by_text(self, heal_svc):
        """无 stable class 时使用文本"""
        page = AsyncMock()
        page.evaluate.return_value = 1

        raw = {
            "tag": "button", "id": "", "name": "",
            "placeholder": "", "textContent": "Login", "className": "",
            "dataTestid": "",
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert 'has-text("Login")' in sel

    @pytest.mark.asyncio
    async def test_selector_fallback_nth_child(self, heal_svc):
        """所有选择器都不唯一时回退 nth-child"""
        page = AsyncMock()
        page.evaluate.return_value = 0  # is_unique → False

        raw = {
            "tag": "div", "id": "", "name": "",
            "placeholder": "", "textContent": "", "className": "",
            "dataTestid": "", "index": 2,
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert "nth-child" in sel

    @pytest.mark.asyncio
    async def test_selector_fallback_tag_only(self, heal_svc):
        """无 index 时仅返回 tag"""
        page = AsyncMock()
        page.evaluate.return_value = 0

        raw = {
            "tag": "span", "id": "", "name": "",
            "placeholder": "", "textContent": "", "className": "",
            "dataTestid": "", "index": -1,
        }

        sel = await heal_svc._generate_selector(page, raw)
        assert sel == "span"


# ═══════════════════════════════════════════════
# _is_unique
# ═══════════════════════════════════════════════

class TestIsUnique:
    """_is_unique() 选择器唯一性检查"""

    @pytest.mark.asyncio
    async def test_unique_selector_returns_true(self):
        """唯一选择器返回 True"""
        page = AsyncMock()
        page.evaluate.return_value = 1

        result = await HealService._is_unique(page, "#unique-btn")
        assert result is True

    @pytest.mark.asyncio
    async def test_non_unique_selector_returns_false(self):
        """不唯一选择器返回 False"""
        page = AsyncMock()
        page.evaluate.return_value = 3

        result = await HealService._is_unique(page, ".many")
        assert result is False

    @pytest.mark.asyncio
    async def test_evaluate_error_returns_false(self):
        """JS 执行错误返回 False"""
        page = AsyncMock()
        page.evaluate.side_effect = Exception("Invalid selector")

        result = await HealService._is_unique(page, ":::bad")
        assert result is False


# ═══════════════════════════════════════════════
# _format_elements_compact
# ═══════════════════════════════════════════════

class TestFormatElementsCompact:
    """_format_elements_compact() 元素列表格式化"""

    def test_formats_single_element(self):
        elements = [
            {
                "type": "button", "tag": "button",
                "el_id": "submit-btn", "name": "", "placeholder": "",
                "text": "Submit", "dataTestid": "", "selector": "#submit-btn",
            },
        ]
        result = HealService._format_elements_compact(elements)
        assert "[button]" in result
        assert "id=submit-btn" in result
        assert 'text="Submit"' in result
        assert "selector=#submit-btn" in result

    def test_formats_multiple_elements(self):
        elements = [
            {
                "type": "text", "tag": "input",
                "el_id": "", "name": "email", "placeholder": "Enter email",
                "text": "", "dataTestid": "", "selector": '[name="email"]',
            },
            {
                "type": "link", "tag": "a",
                "el_id": "", "name": "", "placeholder": "",
                "text": "Home", "dataTestid": "nav-home",
                "selector": '[data-testid="nav-home"]',
            },
        ]
        result = HealService._format_elements_compact(elements)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "name=email" in lines[0]
        assert "placeholder=Enter email" in lines[0]
        assert "data-testid=nav-home" in lines[1]

    def test_empty_list_returns_placeholder(self):
        result = HealService._format_elements_compact([])
        assert "无可用元素" in result

    def test_skips_optional_fields(self):
        """可选字段为空时不显示"""
        elements = [
            {
                "type": "button", "tag": "button",
                "el_id": "", "name": "", "placeholder": "",
                "text": "", "dataTestid": "", "selector": "button:nth-child(1)",
            },
        ]
        result = HealService._format_elements_compact(elements)
        assert "id=" not in result
        assert "name=" not in result
        assert "placeholder=" not in result
        assert 'text="' not in result
        assert "data-testid=" not in result
        assert "selector=button:nth-child(1)" in result


# ═══════════════════════════════════════════════
# _call_heal_ai — 重试逻辑
# ═══════════════════════════════════════════════

class TestCallHealAIRetry:
    """_call_heal_ai() 重试逻辑（有 API Key 时）"""

    def test_retry_then_success(self, heal_svc, mock_settings, mocker):
        """前2次失败，第3次成功"""
        mock_settings("OPENAI_API_KEY", "sk-test-key")
        mock_settings("OPENAI_BASE_URL", "https://api.openai.com/v1")
        mock_settings("OPENAI_MODEL", "gpt-4")

        call_count = [0]

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "healed code here"}}]}

        class MockClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, *args, **kwargs):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise Exception(f"Network error {call_count[0]}")
                return MockResponse()

        mocker.patch("app.services.heal_service.httpx.Client", return_value=MockClient())
        mocker.patch("app.services.heal_service.time.sleep")  # 跳过等待

        result = heal_svc._call_heal_ai("fix this code")
        assert result == "healed code here"
        assert call_count[0] == 3

    def test_all_retries_exhausted(self, heal_svc, mock_settings, mocker):
        """3次重试全部失败抛出异常"""
        mock_settings("OPENAI_API_KEY", "sk-test-key")
        mock_settings("OPENAI_BASE_URL", "https://api.openai.com/v1")
        mock_settings("OPENAI_MODEL", "gpt-4")

        class MockClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, *args, **kwargs):
                raise Exception("Always fails")

        mocker.patch("app.services.heal_service.httpx.Client", return_value=MockClient())
        mocker.patch("app.services.heal_service.time.sleep")

        with pytest.raises(Exception, match="AI 调用失败"):
            heal_svc._call_heal_ai("fix this code")

    def test_retry_with_exponential_backoff(self, heal_svc, mock_settings, mocker):
        """验证重试间隔递增"""
        mock_settings("OPENAI_API_KEY", "sk-test-key")
        mock_settings("OPENAI_BASE_URL", "https://api.openai.com/v1")
        mock_settings("OPENAI_MODEL", "gpt-4")

        sleep_delays = []
        mocker.patch("app.services.heal_service.time.sleep", side_effect=lambda d: sleep_delays.append(d))

        call_count = [0]

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class MockClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, *args, **kwargs):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise Exception("fail")
                return MockResponse()

        mocker.patch("app.services.heal_service.httpx.Client", return_value=MockClient())

        heal_svc._call_heal_ai("fix this code")
        # 第1次失败后 sleep(2^0)=1, 第2次失败后 sleep(2^1)=2
        assert sleep_delays == [1, 2]


# ═══════════════════════════════════════════════
# _retry_execution
# ═══════════════════════════════════════════════

class TestRetryExecution:
    """_retry_execution() 沙箱重试执行"""

    @pytest.mark.asyncio
    async def test_execution_success(self, heal_svc_db, mocker):
        """沙箱执行成功 → 更新步骤状态为 success"""
        # CodeInjector 在 _retry_execution 内部通过 from app.utils.code_injector import CodeInjector 导入
        mock_code_injector = mocker.patch("app.utils.code_injector.CodeInjector")
        mock_code_injector.inject.return_value = "injected code"

        mocker.patch("app.services.playwright_service._MonitorHooks")
        # _build_namespace 必须返回真实 dict，否则 namespace.get("run_test") 会返回 MagicMock
        mocker.patch("app.services.playwright_service._build_namespace", return_value={})

        mock_run_test = AsyncMock(return_value={"success": True, "steps": []})

        def mock_exec(code, ns):
            ns["run_test"] = mock_run_test

        mocker.patch("builtins.exec", side_effect=mock_exec)

        page = AsyncMock()
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )

        result = await heal_svc_db._retry_execution(
            page, "async def run_test(page): pass", step, 1, 1
        )

        assert result is True
        assert step.status == "success"
        assert "HEALED" in step.log_output
        mock_run_test.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execution_timeout(self, heal_svc_db, mocker):
        """执行超时返回 False"""
        mocker.patch("app.utils.code_injector.CodeInjector")
        mocker.patch("app.services.playwright_service._MonitorHooks")
        mocker.patch("app.services.playwright_service._build_namespace", return_value={})

        mock_run_test = AsyncMock(side_effect=Exception("should not be called"))

        def mock_exec(code, ns):
            ns["run_test"] = mock_run_test

        mocker.patch("builtins.exec", side_effect=mock_exec)

        # patch asyncio.wait_for 抛出 TimeoutError，同时关闭传入的 run_test(page) 协程，
        # 避免协程从未被 await 触发 RuntimeWarning
        import asyncio

        def _mock_wait_for(coro, *args, **kwargs):
            coro.close()
            raise asyncio.TimeoutError()

        mocker.patch("asyncio.wait_for", side_effect=_mock_wait_for)

        page = AsyncMock()
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )

        result = await heal_svc_db._retry_execution(
            page, "async def run_test(page): pass", step, 1, 1
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_execution_exception(self, heal_svc_db, mocker):
        """执行异常返回 False 并记录错误"""
        mocker.patch("app.utils.code_injector.CodeInjector")
        mocker.patch("app.services.playwright_service._MonitorHooks")
        mocker.patch("app.services.playwright_service._build_namespace", return_value={})

        mock_run_test = AsyncMock(side_effect=RuntimeError("Something crashed"))

        def mock_exec(code, ns):
            ns["run_test"] = mock_run_test

        mocker.patch("builtins.exec", side_effect=mock_exec)

        page = AsyncMock()
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )

        result = await heal_svc_db._retry_execution(
            page, "async def run_test(page): pass", step, 1, 1
        )

        assert result is False
        assert "自愈重试失败" in step.error_message

    @pytest.mark.asyncio
    async def test_missing_run_test(self, heal_svc_db, mocker):
        """缺少 run_test 函数返回 False"""
        mocker.patch("app.utils.code_injector.CodeInjector")
        mocker.patch("app.services.playwright_service._MonitorHooks")
        mocker.patch("app.services.playwright_service._build_namespace", return_value={})

        def mock_exec(code, ns):
            pass  # 不注入 run_test

        mocker.patch("builtins.exec", side_effect=mock_exec)

        page = AsyncMock()
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )

        result = await heal_svc_db._retry_execution(
            page, "print('hello')", step, 1, 1
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_exec_failure_returns_false(self, heal_svc_db, mocker):
        """exec 本身失败返回 False"""
        mocker.patch("app.utils.code_injector.CodeInjector")
        mocker.patch("app.services.playwright_service._MonitorHooks")
        mocker.patch("app.services.playwright_service._build_namespace", return_value={})

        mocker.patch("builtins.exec", side_effect=SyntaxError("bad code"))

        page = AsyncMock()
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )

        result = await heal_svc_db._retry_execution(
            page, "bad code", step, 1, 1
        )

        assert result is False


# ═══════════════════════════════════════════════
# try_heal
# ═══════════════════════════════════════════════

HEALED_CODE = (
    "from playwright.async_api import Page, expect\n"
    "import asyncio\n"
    "from datetime import datetime\n\n"
    "async def run_test(page: Page) -> dict:\n"
    "    steps_result = []\n"
    "    start_time = datetime.now()\n"
    "    try:\n"
    '        await page.goto("https://example.com")\n'
    '        await page.locator("#btn").click()\n'
    '        steps_result.append({"step": 1, "status": "passed"})\n'
    "    except Exception as e:\n"
    '        return {"success": False, "message": str(e), "steps": steps_result}\n'
    '    return {"success": True, "message": "ok", "steps": steps_result}\n'
)


class TestTryHeal:
    """try_heal() 自动自愈主流程"""

    @pytest.mark.asyncio
    async def test_heal_success_first_attempt(self, heal_svc_db, mocker, sample_generated_code):
        """第一次重试就成功"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout 30000ms exceeded",
            status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=True)

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result is True
        # 验证自愈记录被保存
        heal_record = heal_svc_db._db.query(HealRecord).first()
        assert heal_record is not None
        assert heal_record.retry_status == "success"

    @pytest.mark.asyncio
    async def test_heal_fails_all_retries(self, heal_svc_db, mocker, sample_generated_code):
        """全部重试失败"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout 30000ms exceeded",
            status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=False)

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=page, project_id=1, max_retries=2,
        )

        assert result is False
        assert step.status == "failed"

    @pytest.mark.asyncio
    async def test_heal_unable_to_heal(self, heal_svc_db, mocker, sample_generated_code):
        """AI 返回 UNABLE_TO_HEAL → 停止自愈"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value="UNABLE_TO_HEAL: cannot fix")
        mock_retry = mocker.patch.object(heal_svc_db, "_retry_execution")

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result is False
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_heal_ai_call_exception(self, heal_svc_db, mocker, sample_generated_code):
        """AI 调用异常 → 继续重试"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        # 第1次 AI 调用抛异常，第2次成功
        mocker.patch.object(
            heal_svc_db, "_call_heal_ai",
            side_effect=[Exception("API error"), HEALED_CODE],
        )
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=True)

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result is True


# ═══════════════════════════════════════════════
# try_heal_manual
# ═══════════════════════════════════════════════

class TestTryHealManual:
    """try_heal_manual() 手动自愈流程"""

    @pytest.mark.asyncio
    async def test_manual_heal_success(self, heal_svc_db, mocker, sample_generated_code):
        """手动自愈成功 → 返回 HealResult(success)"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout 30000ms exceeded",
            status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=True)

        result = await heal_svc_db.try_heal_manual(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result.retry_status == "success"
        assert result.heal_id > 0
        assert result.retry_count == 1
        assert "async def run_test" in result.healed_code

    @pytest.mark.asyncio
    async def test_manual_heal_fails_all_retries(self, heal_svc_db, mocker, sample_generated_code):
        """手动自愈全部失败 → 返回 HealResult(failed)"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=False)

        result = await heal_svc_db.try_heal_manual(
            execution_id=1, step=step, page=page, project_id=1, max_retries=2,
        )

        assert result.retry_status == "failed"
        assert result.retry_count == 2
        assert "全部失败" in result.error_message

    @pytest.mark.asyncio
    async def test_manual_heal_ai_exception(self, heal_svc_db, mocker, sample_generated_code):
        """AI 调用异常 → 直接返回 HealResult(failed)"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(
            heal_svc_db, "_call_heal_ai",
            side_effect=Exception("API down"),
        )

        result = await heal_svc_db.try_heal_manual(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result.retry_status == "failed"
        assert "AI 调用失败" in result.error_message

    @pytest.mark.asyncio
    async def test_manual_heal_unable_to_heal(self, heal_svc_db, mocker, sample_generated_code):
        """AI 返回 UNABLE_TO_HEAL → 直接返回 HealResult(failed)"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value="UNABLE_TO_HEAL")

        result = await heal_svc_db.try_heal_manual(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result.retry_status == "failed"
        assert "无法修复" in result.error_message

    @pytest.mark.asyncio
    async def test_manual_heal_validation_error_then_success(self, heal_svc_db, mocker, sample_generated_code):
        """第1次校验失败，第2次成功"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        invalid_code = "import os\nasync def run_test(page):\n    return {}"
        mocker.patch.object(
            heal_svc_db, "_call_heal_ai",
            side_effect=[invalid_code, HEALED_CODE],
        )
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=True)

        result = await heal_svc_db.try_heal_manual(
            execution_id=1, step=step, page=page, project_id=1, max_retries=3,
        )

        assert result.retry_status == "success"


# ═══════════════════════════════════════════════
# _css_escape / _filter_stable_classes
# ═══════════════════════════════════════════════

class TestCssEscape:
    """_css_escape() CSS 选择器转义"""

    def test_escapes_colon(self):
        assert _css_escape("a:b") == "a\\:b"

    def test_escapes_dot(self):
        assert _css_escape("a.b") == "a\\.b"

    def test_escapes_hash(self):
        assert _css_escape("a#b") == "a\\#b"

    def test_no_special_chars(self):
        assert _css_escape("simple-id") == "simple-id"


class TestFilterStableClasses:
    """_filter_stable_classes() 过滤稳定 CSS class"""

    def test_keeps_stable_classes(self):
        result = _filter_stable_classes(["btn", "primary", "large"])
        assert result == ["btn", "primary", "large"]

    def test_filters_dynamic_css_modules(self):
        """过滤 css-xxxxx 格式的动态 class"""
        result = _filter_stable_classes(["btn", "css-1a2b3c", "primary"])
        assert result == ["btn", "primary"]

    def test_filters_hash_like_classes(self):
        """过滤 _xxxxxx 和 xxx-abcdef 格式的动态 class"""
        result = _filter_stable_classes(["btn", "_abc1234", "text-1a2b3c", "ok"])
        assert result == ["btn", "ok"]

    def test_filters_scoped_classes(self):
        """过滤 sc- 开头的 class"""
        result = _filter_stable_classes(["sc-bdVaJa", "container", "sc-htpNat"])
        assert result == ["container"]

    def test_filters_short_classes(self):
        """过滤长度 < 2 的 class"""
        result = _filter_stable_classes(["a", "", "btn", "x"])
        assert result == ["btn"]

    def test_filters_long_ant_classes(self):
        """过滤 ant- 开头且长度 > 20 的 class"""
        result = _filter_stable_classes(["ant-btn", "ant-btn-primary-very-long-name-for-testing", "form-item"])
        assert result == ["ant-btn", "form-item"]

    def test_empty_input(self):
        result = _filter_stable_classes([])
        assert result == []


# ═══════════════════════════════════════════════
# _parse_android_elements / _format_android_elements
# ═══════════════════════════════════════════════

class TestAndroidElements:
    """Android page_source 解析与格式化"""

    PAGE_SOURCE = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hierarchy rotation="0">'
        '<android.widget.FrameLayout package="com.example.app" bounds="[0,0][1080,1920]">'
        '  <android.widget.Button resource-id="com.example.app:id/login_btn" '
        'clickable="true" enabled="true" text="登录" content-desc="login button" '
        'bounds="[100,500][500,700]"/>'
        '  <android.widget.EditText resource-id="com.example.app:id/username" '
        'clickable="false" enabled="true" text="请输入用户名"/>'
        '  <android.widget.TextView resource-id="com.example.app:id/label" '
        'clickable="false" enabled="true" text="欢迎"/>'
        '  <android.widget.ImageView bounds="[0,0][100,100]"/>'
        '</android.widget.FrameLayout>'
        '</hierarchy>'
    )

    def test_parse_android_elements_basic(self):
        """解析出可交互元素（clickable/text/content-desc）"""
        elements = _parse_android_elements(self.PAGE_SOURCE)
        # Button(text+clickable) + EditText(text) + TextView(text) = 3
        # ImageView 无 text/clickable/content-desc → 排除
        assert len(elements) == 3
        login = elements[0]
        assert login["text"] == "登录"
        assert login["class_name"] == "Button"
        assert login["clickable"] == "true"
        assert login["enabled"] == "true"
        assert login["bounds"] == "[100,500][500,700]"
        # 当前实现限制：_ANDROID_ATTR_PATTERN 使用 \w+，无法匹配
        # resource-id / content-desc 等带连字符属性名
        assert login["resource_id"] == ""
        assert login["content_desc"] == ""
        assert login["clickable"] == "true"

    def test_parse_android_empty_source(self):
        """空 page_source → 空列表"""
        assert _parse_android_elements("") == []
        assert _parse_android_elements("(无法获取 Page Source)") == []

    def test_parse_android_no_tags(self):
        """无 XML 标签 → 空列表"""
        assert _parse_android_elements("no xml here") == []

    def test_format_android_elements(self):
        """格式化包含完整字段的元素"""
        elements = [{
            "resource_id": "com.example.app:id/login_btn",
            "content_desc": "login button",
            "text": "登录",
            "class_name": "Button",
            "bounds": "[100,500][500,700]",
            "enabled": "true",
            "clickable": "true",
            "package": "com.example.app",
        }]
        result = _format_android_elements(elements)
        assert "[Button]" in result
        assert "resource-id=com.example.app:id/login_btn" in result
        assert "content-desc=login button" in result
        assert 'text="登录"' in result
        assert "bounds=[100,500][500,700]" in result
        assert "clickable" in result

    def test_format_android_elements_empty(self):
        """空元素列表 → 占位文本"""
        assert _format_android_elements([]) == "（无可用元素）"


# ═══════════════════════════════════════════════
# _save_heal_record / _update_heal_record — Attempt 追踪
# ═══════════════════════════════════════════════

class TestHealAttempts:
    """_save_heal_record() 和 _update_heal_record() 的 attempt 追踪"""

    def test_attempt_1_created(self, db_session, sample_project, sample_test_case, sample_execution):
        """第一次保存创建 attempt 1"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        record = svc._save_heal_record(
            step_id=step.id,
            original_code="await page.click('#btn')",
            error_ctx={"error": "timeout", "type": "TimeoutError"},
            healed_code="await page.click('#new-btn')",
            prompt="fix this",
            retry_count=1,
        )

        attempts = json.loads(record.attempts)
        assert len(attempts) == 1
        assert attempts[0]["attempt"] == 1
        assert attempts[0]["status"] == "retrying"
        assert "await page.click('#new-btn')" in attempts[0]["generated_code"]

    def test_attempt_2_updated(self, db_session, sample_project, sample_test_case, sample_execution):
        """第二次更新时 attempts 状态变更"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        record = svc._save_heal_record(
            step_id=step.id,
            original_code="await page.click('#btn')",
            error_ctx={"error": "timeout"},
            healed_code="await page.click('#new-btn')",
            prompt="fix this",
            retry_count=2,
        )

        # 初始状态为 retrying
        attempts = json.loads(record.attempts)
        assert attempts[0]["status"] == "retrying"

        # 更新为 success
        svc._update_heal_record(record.id, "success")
        db_session.refresh(record)
        attempts = json.loads(record.attempts)
        assert attempts[0]["status"] == "success"
        assert attempts[0]["error"] == ""

    def test_attempts_order(self, db_session, sample_project, sample_test_case, sample_execution):
        """多记录按创建时间有序"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        r1 = svc._save_heal_record(
            step_id=step.id, original_code="code1", error_ctx={},
            healed_code="fixed1", prompt="p1", retry_count=1,
        )
        r2 = svc._save_heal_record(
            step_id=step.id, original_code="code2", error_ctx={},
            healed_code="fixed2", prompt="p2", retry_count=2,
        )

        assert r1.id < r2.id
        assert json.loads(r1.attempts)[0]["attempt"] == 1
        assert json.loads(r2.attempts)[0]["attempt"] == 2


# ═══════════════════════════════════════════════
# Android 自愈上下文
# ═══════════════════════════════════════════════

class TestHealAndroidContext:
    """Android 自愈上下文"""

    def test_android_context_fields(self, db_session, sample_project, sample_test_case, sample_execution):
        """Android 上下文包含所有 9 个必填字段"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        android_ctx = {
            "action": "click",
            "target": "#btn",
            "value": "",
            "error_type": "TimeoutError",
            "error_message": "Timeout 30000ms exceeded",
            "exception_type": "TimeoutException",
            "selector_type": "xpath",
            "page_source": '<?xml version="1.0"?><hierarchy><node class="android.widget.Button" text="Submit"/></hierarchy>',
            "screenshot_before": "/path/to/before.png",
            "screenshot_after": "/path/to/after.png",
            "visible_elements": '[Button] text="Submit" clickable',
        }

        svc = HealService(db_session)
        record = svc._save_heal_record(
            step_id=step.id,
            original_code="def run_test(driver): pass",
            error_ctx=android_ctx,
            healed_code="def run_test(driver):\n    driver.find_element()",
            prompt="fix android test",
            retry_count=1,
        )

        ctx = json.loads(record.error_context)
        assert ctx["action"] == "click"
        assert ctx["error_type"] == "TimeoutError"
        assert ctx["error_message"] == "Timeout 30000ms exceeded"
        assert ctx["exception_type"] == "TimeoutException"
        assert ctx["selector_type"] == "xpath"
        assert "page_source" in ctx
        assert "screenshot_before" in ctx
        assert "screenshot_after" in ctx
        assert "visible_elements" in ctx
        assert len(ctx) >= 9

    def test_android_heal_prompt_build(self, heal_svc):
        """Android 自愈 Prompt 构建正确"""
        from unittest.mock import MagicMock

        step = MagicMock()
        step.step_index = 1
        step.action = "click"
        step.target_selector = "#btn"

        error_ctx = {
            "action": "click",
            "target": "#btn",
            "value": "",
            "error_type": "TimeoutError",
            "error_message": "Timeout 30000ms exceeded",
            "exception_type": "TimeoutException",
            "selector_type": "xpath",
            "page_source": "<hierarchy><node class=\"Button\"/></hierarchy>",
            "screenshot_before": "/path/to/before.png",
            "screenshot_after": "/path/to/after.png",
            "visible_elements": "[Button] text=Submit",
        }
        original_code = "def run_test(driver): pass"

        prompt = heal_svc._build_heal_prompt(error_ctx, original_code, step, platform="android")

        assert original_code in prompt
        assert "1" in prompt
        assert "click" in prompt
        assert "#btn" in prompt
        assert "Timeout 30000ms exceeded" in prompt
        assert "TimeoutException" in prompt
        assert "xpath" in prompt
        assert "<hierarchy>" in prompt
        assert "[Button]" in prompt
        # 确认没有残留占位符
        assert "{original_code}" not in prompt
        assert "{error_message}" not in prompt
        assert "{exception_type}" not in prompt
        assert "{page_source}" not in prompt
        assert "{visible_elements}" not in prompt
        # 不应包含 Web 占位符
        assert "{dom_snapshot}" not in prompt
        assert "{elements_list}" not in prompt


# ═══════════════════════════════════════════════
# 自愈记录持久化
# ═══════════════════════════════════════════════

class TestHealHistoryPersistence:
    """自愈记录持久化查询"""

    def test_heal_record_persists(self, db_session, sample_project, sample_test_case, sample_execution):
        """自愈记录保存后可查询回"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        record = svc._save_heal_record(
            step_id=step.id,
            original_code="await page.click('#btn')",
            error_ctx={"error": "timeout"},
            healed_code="await page.click('#new-btn')",
            prompt="fix this",
            retry_count=1,
        )

        # 按 ID 查询
        queried = db_session.query(HealRecord).filter(HealRecord.id == record.id).first()
        assert queried is not None
        assert queried.id == record.id
        assert queried.original_code == "await page.click('#btn')"
        assert queried.healed_code == "await page.click('#new-btn')"
        assert queried.retry_status == "retrying"
        assert queried.retry_count == 1

    def test_heal_records_by_execution(self, db_session, sample_project, sample_test_case, sample_execution):
        """可按 execution_step_id 查询自愈记录"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)
        svc._save_heal_record(
            step_id=step.id, original_code="code1", error_ctx={},
            healed_code="fixed1", prompt="p1", retry_count=1,
        )
        svc._save_heal_record(
            step_id=step.id, original_code="code2", error_ctx={},
            healed_code="fixed2", prompt="p2", retry_count=2,
        )

        records = (
            db_session.query(HealRecord)
            .filter(HealRecord.execution_step_id == step.id)
            .order_by(HealRecord.retry_count)
            .all()
        )
        assert len(records) == 2
        assert records[0].retry_count == 1
        assert records[1].retry_count == 2


# ═══════════════════════════════════════════════
# 原始代码 — 自愈复用
# ═══════════════════════════════════════════════

class TestHealOriginalCode:
    """自愈过程中原始代码的获取与不变性"""

    def test_uses_original_code_from_db(self, heal_svc_db, sample_generated_code):
        """自愈优先使用 GeneratedCode 表中的原始代码"""
        code = heal_svc_db._get_original_code(sample_generated_code.case_id)
        assert code == sample_generated_code.code_content
        assert "async def run_test" in code

    def test_original_code_unchanged_across_attempts(self, db_session, sample_project, sample_test_case, sample_execution, sample_generated_code):
        """多次自愈不会修改原始代码"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        svc = HealService(db_session)

        # 第一次自愈
        original = svc._get_original_code(sample_generated_code.case_id)
        assert original == sample_generated_code.code_content

        # 模拟两次自愈保存
        svc._save_heal_record(
            step_id=step.id, original_code=original, error_ctx={},
            healed_code="fixed1", prompt="p1", retry_count=1,
        )

        # 原始代码不变
        original_again = svc._get_original_code(sample_generated_code.case_id)
        assert original_again == original

        # 第二次自愈后原始代码仍不变
        svc._save_heal_record(
            step_id=step.id, original_code=original, error_ctx={},
            healed_code="fixed2", prompt="p2", retry_count=2,
        )

        final_original = svc._get_original_code(sample_generated_code.case_id)
        assert final_original == sample_generated_code.code_content
        assert "fixed" not in final_original


# ═══════════════════════════════════════════════
# 自愈入口页面健康检查（目标环境不可达时跳过）
# ═══════════════════════════════════════════════

class TestCheckEnvReachable:
    """_check_env_reachable() — 自愈前目标环境健康检查"""

    @pytest.mark.asyncio
    async def test_web_reachable_returns_true(self, heal_svc_db, sample_project):
        """Web 目标可达（goto 成功）→ True"""
        page = AsyncMock()
        page.goto = AsyncMock()

        result = await heal_svc_db._check_env_reachable(page, sample_project.id, "web")
        assert result is True
        page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_web_unreachable_returns_false(self, heal_svc_db, sample_project):
        """Web 目标不可达（goto 抛异常）→ False"""
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_CONNECTION_REFUSED"))

        result = await heal_svc_db._check_env_reachable(page, sample_project.id, "web")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_target_url_returns_true(self, heal_svc_db):
        """项目不存在/无 target_url → 不拦截，返回 True"""
        page = AsyncMock()

        result = await heal_svc_db._check_env_reachable(page, 99999, "web")
        assert result is True
        page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_android_reachable_returns_true(self, heal_svc_db):
        """Android driver 存活（page_source 正常）→ True"""
        page = AsyncMock()
        page.page_source = "<hierarchy></hierarchy>"

        result = await heal_svc_db._check_env_reachable(page, 1, "android")
        assert result is True

    @pytest.mark.asyncio
    async def test_android_unreachable_returns_false(self, heal_svc_db):
        """Android driver 断开（page_source 访问抛异常）→ False"""
        class _BrokenDriver:
            @property
            def page_source(self):
                raise Exception("driver disconnected")

        result = await heal_svc_db._check_env_reachable(_BrokenDriver(), 1, "android")
        assert result is False


# ═══════════════════════════════════════════════
# 快速失败 — 同一 step 同类错误去重
# ═══════════════════════════════════════════════

class TestQuickFail:
    """同一 step 的同类错误连续失败达到阈值后跳过自愈"""

    @pytest.fixture(autouse=True)
    def _clean_failure_cache(self):
        from app.services.heal_service import _HEAL_FAILURE_CACHE
        _HEAL_FAILURE_CACHE.clear()
        yield
        _HEAL_FAILURE_CACHE.clear()

    def test_track_failure_increments_count(self, heal_svc):
        from app.services.heal_service import _HEAL_FAILURE_CACHE
        HealService._track_heal_failure("step1_TimeoutError")
        HealService._track_heal_failure("step1_TimeoutError")
        assert _HEAL_FAILURE_CACHE.get("step1_TimeoutError") == 2

    def test_track_failure_respects_capacity(self, heal_svc):
        from app.services.heal_service import _HEAL_FAILURE_CACHE, _HEAL_FAILURE_CACHE_MAX_SIZE
        for i in range(_HEAL_FAILURE_CACHE_MAX_SIZE):
            _HEAL_FAILURE_CACHE[f"k{i}"] = 1
        HealService._track_heal_failure("new_key")
        # 超限触发清空，只保留最新计数
        assert _HEAL_FAILURE_CACHE.get("new_key") == 1
        assert len(_HEAL_FAILURE_CACHE) == 1

    @pytest.mark.asyncio
    async def test_try_heal_skips_after_threshold(
        self, heal_svc_db, mocker, sample_generated_code
    ):
        """同一 step 同类错误连续失败达到阈值 → 后续直接跳过（不调用 AI）"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []
        mocker.patch.object(heal_svc_db, "_check_env_reachable", return_value=True)
        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=False)

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout 30000ms exceeded", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        from app.config import settings
        for _ in range(settings.HEAL_MAX_RETRY_SAME_ERROR):
            result = await heal_svc_db.try_heal(
                execution_id=1, step=step, page=page, project_id=1, max_retries=1,
            )
            assert result is False

        # 达到阈值后，下一次直接快速失败，AI 不再被调用
        heal_svc_db._call_heal_ai.reset_mock()
        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=page, project_id=1, max_retries=1,
        )
        assert result is False
        heal_svc_db._call_heal_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_clears_failure_count(
        self, heal_svc_db, mocker, sample_generated_code
    ):
        """自愈成功后清除快速失败计数"""
        page = AsyncMock()
        page.content.return_value = "<html></html>"
        page.evaluate.return_value = []
        mocker.patch.object(heal_svc_db, "_check_env_reachable", return_value=True)
        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_retry_execution", return_value=True)

        step = ExecutionStep(
            execution_id=1, case_id=sample_generated_code.case_id, step_index=1,
            action="click", target_selector="#old-btn",
            error_message="Timeout", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        from app.services.heal_service import _HEAL_FAILURE_CACHE
        _HEAL_FAILURE_CACHE["1_TimeoutError"] = 2  # 预置 2 次失败

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=page, project_id=1, max_retries=1,
        )
        assert result is True
        # 成功后计数被清除（或不在缓存中）
        assert "1_TimeoutError" not in _HEAL_FAILURE_CACHE


# ═══════════════════════════════════════════════
# AI 调用限流（熔断）
# ═══════════════════════════════════════════════

class TestHealRateLimit:
    """_call_heal_ai() 限流熔断"""

    @pytest.fixture(autouse=True)
    def _clean_rate_limiter(self):
        from app.services.heal_service import ai_rate_limiter
        from app.config import settings
        orig_max = ai_rate_limiter._max_calls
        ai_rate_limiter._calls.clear()
        yield
        ai_rate_limiter._calls.clear()
        ai_rate_limiter._max_calls = orig_max

    def test_mock_mode_does_not_consume_quota(self, heal_svc):
        """Mock 模式（无 API Key）不消耗限流额度"""
        from app.services.heal_service import ai_rate_limiter
        result = heal_svc._call_heal_ai("fix this code")
        assert "run_test" in result
        assert len(ai_rate_limiter._calls) == 0

    def test_circuit_breaker_raises_when_window_full(self, heal_svc, mock_settings):
        """窗口已满（每分钟上限）→ 抛熔断异常"""
        from app.services.heal_service import ai_rate_limiter
        mock_settings("OPENAI_API_KEY", "sk-test-key")
        ai_rate_limiter._max_calls = 3  # 临时降低上限

        # 手动填满滑动窗口（3 次）
        import time
        now = time.time()
        for _ in range(3):
            ai_rate_limiter._calls.append(now)

        with pytest.raises(Exception, match="熔断"):
            heal_svc._call_heal_ai("fix this code")


# ═══════════════════════════════════════════════
# try_heal / try_heal_manual 入口边界
# ═══════════════════════════════════════════════

class TestTryHealEntryBoundaries:
    """try_heal / try_heal_manual 入口边界（环境不可达 / 校验失败 / 快速失败）"""

    def _failed_step(self, db, error_msg="Timeout 30000ms exceeded"):
        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", target_selector="#btn",
            error_message=error_msg, status="failed",
        )
        db.add(step)
        db.commit()
        return step

    @pytest.mark.asyncio
    async def test_try_heal_env_unreachable_returns_false(self, heal_svc_db, mocker):
        """环境不可达 → try_heal 直接返回 False，不调用 AI"""
        step = self._failed_step(heal_svc_db._db)
        mocker.patch.object(heal_svc_db, "_check_env_reachable", return_value=False)
        mock_ai = mocker.patch.object(heal_svc_db, "_call_heal_ai")

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=AsyncMock(), project_id=1, max_retries=3,
        )

        assert result is False
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_try_heal_validation_error_skips_retry(self, heal_svc_db, mocker):
        """AI 修复代码校验失败 → 继续下一轮重试，不执行修复代码"""
        step = self._failed_step(heal_svc_db._db)
        mocker.patch.object(heal_svc_db, "_call_heal_ai", return_value=HEALED_CODE)
        mocker.patch.object(heal_svc_db, "_validate_healed", return_value="修复代码不安全")
        mock_retry = mocker.patch.object(heal_svc_db, "_retry_execution")

        result = await heal_svc_db.try_heal(
            execution_id=1, step=step, page=AsyncMock(), project_id=1, max_retries=2,
        )

        assert result is False
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_try_heal_manual_env_unreachable(self, heal_svc_db, mocker):
        """手动自愈：环境不可达 → 返回 HealResult(failed)"""
        step = self._failed_step(heal_svc_db._db)
        mocker.patch.object(heal_svc_db, "_check_env_reachable", return_value=False)

        result = await heal_svc_db.try_heal_manual(
            execution_id=1, step=step, page=AsyncMock(), project_id=1,
        )

        assert result.retry_status == "failed"
        assert "不可达" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_try_heal_manual_quick_fail(self, heal_svc_db):
        """手动自愈：同类错误已达快速失败阈值 → 直接返回 HealResult(failed)"""
        from app.services.heal_service import _HEAL_FAILURE_CACHE
        from app.config import settings

        step = self._failed_step(heal_svc_db._db)
        cache_key = f"{step.id}_TimeoutError"
        _HEAL_FAILURE_CACHE[cache_key] = settings.HEAL_MAX_RETRY_SAME_ERROR
        try:
            result = await heal_svc_db.try_heal_manual(
                execution_id=1, step=step, page=AsyncMock(), project_id=1,
            )
            assert result.retry_status == "failed"
            assert "连续失败" in (result.error_message or "")
        finally:
            _HEAL_FAILURE_CACHE.pop(cache_key, None)


# ═══════════════════════════════════════════════
# Android 上下文异常降级
# ═══════════════════════════════════════════════

class TestAndroidContextFailure:
    """Android 上下文捕获 — page_source 获取失败降级"""

    @pytest.mark.asyncio
    async def test_page_source_failure_fallback(self, heal_svc_db):
        class _BrokenDriver:
            @property
            def page_source(self):
                raise Exception("driver disconnected")

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
            exception_type="NoSuchElementException",
        )
        ctx = await heal_svc_db._capture_failure_context(
            step, _BrokenDriver(), platform="android"
        )
        assert ctx["exception_type"] == "NoSuchElementException"
        assert ctx["page_source"] == "(无法获取 Page Source)"
        assert ctx["visible_elements"] == "(无法获取页面元素)"

    @pytest.mark.asyncio
    async def test_page_source_success_extracts_elements(self, heal_svc_db):
        """page_source 正常 → 提取 page_source 与可见元素"""
        page_source = (
            '<?xml version="1.0"?><hierarchy>'
            '<android.widget.Button resource-id="com.example:id/btn" '
            'clickable="true" enabled="true" text="登录" bounds="[0,0][10,10]"/>'
            "</hierarchy>"
        )

        class _FakeDriver:
            @property
            def page_source(self):
                return page_source

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
            exception_type="NoSuchElementException",
        )
        ctx = await heal_svc_db._capture_failure_context(
            step, _FakeDriver(), platform="android"
        )
        assert ctx["page_source"] == page_source
        assert "[Button]" in ctx["visible_elements"]
        assert "登录" in ctx["visible_elements"]


# ═══════════════════════════════════════════════
# _build_heal_prompt 模板回退
# ═══════════════════════════════════════════════

class TestBuildHealPromptFallback:
    """_build_heal_prompt() 模板文件不存在 → 使用内置默认模板"""

    def test_fallback_template_when_file_missing(self, heal_svc, mocker):
        from unittest.mock import MagicMock
        mocker.patch("os.path.exists", return_value=False)

        step = MagicMock()
        step.step_index = 1
        step.action = "click"
        step.target_selector = "#btn"

        prompt = heal_svc._build_heal_prompt(
            {"error_message": "boom"}, "async def run_test(page): pass", step
        )
        assert "修复以下测试代码中的失败步骤" in prompt
        assert "async def run_test(page): pass" in prompt
        assert "boom" in prompt


# ═══════════════════════════════════════════════
# _call_heal_ai / _mock_heal_response — Android
# ═══════════════════════════════════════════════

class TestCallHealAIPlatform:
    """_call_heal_ai() Android 分支"""

    def test_mock_response_android(self):
        """Mock 模式 Android 响应 → 同步 run_test(driver)"""
        result = HealService._mock_heal_response(platform="android")
        assert "def run_test(driver)" in result
        assert "async" not in result

    def test_mock_response_web(self):
        """Mock 模式 Web 响应 → 异步 run_test(page)"""
        result = HealService._mock_heal_response(platform="web")
        assert "async def run_test(page" in result

    def test_android_system_message(self, heal_svc, mock_settings, mocker):
        """Android 分支使用 Appium 专家 system 消息"""
        mock_settings("OPENAI_API_KEY", "test-key")
        from app.services.heal_service import ai_rate_limiter
        ai_rate_limiter._calls.clear()

        resp = mocker.MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "choices": [{"message": {"content": "def run_test(driver): pass"}}]
        }
        mock_client = mocker.MagicMock()
        mock_client.__enter__.return_value.post.return_value = resp
        mocker.patch("app.services.heal_service.httpx.Client", return_value=mock_client)
        mocker.patch("app.services.heal_service.time.sleep")

        try:
            result = heal_svc._call_heal_ai("fix", platform="android")
            assert "run_test(driver)" in result
        finally:
            ai_rate_limiter._calls.clear()


# ═══════════════════════════════════════════════
# _retry_execution — Android 分发 + inject 失败
# ═══════════════════════════════════════════════

class TestRetryExecutionAndroid:
    """_retry_execution() Android 分支"""

    @pytest.mark.asyncio
    async def test_android_dispatches_to_sync(self, heal_svc_db, mocker):
        """platform=android → 分发到 _retry_execution_sync"""
        mock_sync = mocker.patch.object(
            heal_svc_db, "_retry_execution_sync", return_value=True
        )
        result = await heal_svc_db._retry_execution(
            AsyncMock(), "code", MagicMock(), 1, 1, platform="android"
        )
        assert result is True
        mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_inject_failure_uses_original_code(self, heal_svc_db, mocker):
        """CodeInjector.inject 抛 SecurityException → 使用原始代码继续"""
        from app.exceptions import SecurityException
        mock_code_injector = mocker.patch("app.utils.code_injector.CodeInjector")
        mock_code_injector.inject.side_effect = SecurityException("inject failed")
        mocker.patch("app.services.playwright_service._MonitorHooks")
        mocker.patch("app.services.playwright_service._build_namespace", return_value={})

        mock_run_test = AsyncMock(return_value={"success": True})

        def mock_exec(code, ns):
            ns["run_test"] = mock_run_test

        mocker.patch("builtins.exec", side_effect=mock_exec)

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )
        result = await heal_svc_db._retry_execution(
            AsyncMock(), "async def run_test(page): pass", step, 1, 1
        )
        assert result is True


# ═══════════════════════════════════════════════
# _retry_execution_sync — Android 同步重试
# ═══════════════════════════════════════════════

class TestRetryExecutionSync:
    """_retry_execution_sync() Android 同步沙箱重试"""

    def test_sync_success(self, heal_svc_db, mocker, mock_file_ops):
        """同步重试成功 → step 标记 success"""
        mocker.patch("app.utils.appium_code_injector.AppiumCodeInjector")
        mocker.patch("app.services.appium_service._build_sync_namespace", return_value={})

        def mock_exec(code, ns):
            ns["run_test"] = lambda driver: {"success": True}

        mocker.patch("builtins.exec", side_effect=mock_exec)

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        result = heal_svc_db._retry_execution_sync(
            AsyncMock(), "def run_test(driver): pass", step, 1, 1
        )
        assert result is True
        heal_svc_db._db.refresh(step)
        assert step.status == "success"
        assert "HEALED" in (step.log_output or "")

    def test_sync_run_test_returns_false(self, heal_svc_db, mocker, mock_file_ops):
        """run_test 返回 success=False → 返回 False"""
        mocker.patch("app.utils.appium_code_injector.AppiumCodeInjector")
        mocker.patch("app.services.appium_service._build_sync_namespace", return_value={})

        def mock_exec(code, ns):
            ns["run_test"] = lambda driver: {"success": False}

        mocker.patch("builtins.exec", side_effect=mock_exec)

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        result = heal_svc_db._retry_execution_sync(
            AsyncMock(), "def run_test(driver): pass", step, 1, 1
        )
        assert result is False

    def test_sync_run_test_exception(self, heal_svc_db, mocker, mock_file_ops):
        """run_test 抛异常 → 记录 error_message 并返回 False"""
        mocker.patch("app.utils.appium_code_injector.AppiumCodeInjector")
        mocker.patch("app.services.appium_service._build_sync_namespace", return_value={})

        def mock_exec(code, ns):
            def run_test(driver):
                raise RuntimeError("boom")
            ns["run_test"] = run_test

        mocker.patch("builtins.exec", side_effect=mock_exec)

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        result = heal_svc_db._retry_execution_sync(
            AsyncMock(), "def run_test(driver): pass", step, 1, 1
        )
        assert result is False
        heal_svc_db._db.refresh(step)
        assert "自愈重试失败" in (step.error_message or "")

    def test_sync_inject_failure_uses_original(self, heal_svc_db, mocker, mock_file_ops):
        """Android 注入失败 → 使用原始代码继续"""
        from app.exceptions import SecurityException
        mock_injector = mocker.patch("app.utils.appium_code_injector.AppiumCodeInjector")
        mock_injector.inject.side_effect = SecurityException("inject failed")
        mocker.patch("app.services.appium_service._build_sync_namespace", return_value={})

        def mock_exec(code, ns):
            ns["run_test"] = lambda driver: {"success": True}

        mocker.patch("builtins.exec", side_effect=mock_exec)

        step = ExecutionStep(
            execution_id=1, case_id=1, step_index=1,
            action="click", status="failed",
        )
        heal_svc_db._db.add(step)
        heal_svc_db._db.commit()

        result = heal_svc_db._retry_execution_sync(
            AsyncMock(), "def run_test(driver): pass", step, 1, 1
        )
        assert result is True


# ═══════════════════════════════════════════════
# _update_heal_record 边界
# ═══════════════════════════════════════════════

class TestUpdateHealRecordEdgeCases:
    """_update_heal_record() 边界分支"""

    def test_record_not_found(self, heal_svc_db):
        """记录不存在 → 静默跳过"""
        heal_svc_db._update_heal_record(99999, "success")  # 不应抛异常

    def test_invalid_attempts_json(self, db_session, sample_project, sample_test_case, sample_execution):
        """attempts 字段为无效 JSON → 回退为空，不崩溃"""
        step = ExecutionStep(
            execution_id=sample_execution.id, case_id=sample_test_case.id, step_index=1,
            action="click", status="failed",
        )
        db_session.add(step)
        db_session.commit()

        record = HealRecord(
            execution_step_id=step.id,
            original_code="code",
            error_context="{}",
            healed_code="new code",
            heal_prompt="prompt",
            retry_status="retrying",
            retry_count=1,
            attempts="not json {{{",
        )
        db_session.add(record)
        db_session.commit()
        db_session.refresh(record)

        svc = HealService(db_session)
        svc._update_heal_record(record.id, "success")

        db_session.refresh(record)
        assert record.retry_status == "success"