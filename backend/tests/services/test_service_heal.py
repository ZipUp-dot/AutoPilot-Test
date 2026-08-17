"""自愈服务测试 — pytest 风格，覆盖错误分类、代码提取、校验、AI 调用、DB 记录"""

import json
from unittest.mock import MagicMock, AsyncMock

import pytest

from app.services.heal_service import HealService, _css_escape, _filter_stable_classes
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