"""AI 代码生成服务单元测试"""

import json
import pytest

from app.services.ai_service import (
    AIService,
    GenerateResult,
    BatchJob,
    _build_prompt,
    _extract_code,
    _validate_syntax,
    _security_check,
    _mock_code,
    _mock_android_code,
    _call_openai,
    _call_openai_vision,
    _format_elements,
)
from app.exceptions import AIException, SecurityException
from app.models.generated_code import GeneratedCode
from app.models.element import PageElement


# ═══════════════════════════════════════════════
# generate_single() 测试
# ═══════════════════════════════════════════════

class TestGenerateSingle:
    """generate_single() 核心流程测试"""

    def test_generate_single_valid_case(self, db_session, sample_project, sample_test_case):
        """场景1: generate_single() 有效用例 -> 返回 code_id > 0, is_valid=True"""
        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)

        assert isinstance(result, GenerateResult)
        assert result.code_id is not None
        assert result.code_id > 0
        assert result.is_valid is True
        assert result.syntax_error is None

    def test_generate_single_nonexistent_case(self, db_session, sample_project):
        """场景2: generate_single() 不存在用例 -> 抛出 AIException"""
        svc = AIService(db_session)
        with pytest.raises(AIException, match="用例 99999 不存在"):
            svc.generate_single(sample_project.id, 99999)

    def test_generate_single_no_steps(self, db_session, sample_project):
        """场景3: generate_single() 用例无步骤 -> 抛出 AIException"""
        from app.models.test_case import TestCase
        case = TestCase(
            project_id=sample_project.id,
            case_name="Empty Case",
            steps=json.dumps([]),
            status="pending",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        svc = AIService(db_session)
        with pytest.raises(AIException, match="无步骤数据"):
            svc.generate_single(sample_project.id, case.id)

    def test_generate_single_stores_code(self, db_session, sample_project, sample_test_case, mock_llm):
        """场景4: generate_single() 使用 mock_llm -> 代码存储到 generated_codes 表"""
        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)

        assert result.code_id > 0
        code = db_session.query(GeneratedCode).filter(GeneratedCode.id == result.code_id).first()
        assert code is not None
        assert "async def run_test" in code.code_content
        assert code.is_valid == 1
        assert code.case_id == sample_test_case.id

    def test_generate_single_invalid_code(self, db_session, sample_project, sample_test_case, mock_llm_invalid_code):
        """场景5: generate_single() 返回语法错误代码 -> is_valid=False, syntax_error 设置"""
        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)

        assert result.is_valid is False
        assert result.syntax_error is not None
        assert result.code_id is not None
        assert result.code_id > 0

    def test_generate_single_network_error(self, db_session, sample_project, sample_test_case, mock_llm_network_error):
        """场景6: generate_single() 网络错误 -> 抛出 AIException"""
        svc = AIService(db_session)
        with pytest.raises(AIException, match="AI 服务调用失败"):
            svc.generate_single(sample_project.id, sample_test_case.id)

    def test_generate_single_security_error(self, db_session, sample_project, sample_test_case, mocker):
        """generate_single() 返回含危险导入的代码 -> is_valid=False, syntax_error 设为安全消息"""
        mocker.patch(
            "app.services.ai_service._call_openai",
            return_value="async def run_test(page):\n    import os\n    return {'success': True}",
        )
        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)

        assert result.is_valid is False
        assert result.syntax_error is not None
        assert "os" in result.syntax_error.lower()


# ═══════════════════════════════════════════════
# _match_elements() 测试
# ═══════════════════════════════════════════════

class TestMatchElements:
    """元素匹配测试"""

    def test_match_elements_exact_match(self, db_session, sample_project):
        """场景7: _match_elements() 精确匹配 -> 使用元素的 selector"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#login-btn",
            text_content="登录",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "登录", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#login-btn"
        assert result[0]["_matched_selector"] == "#login-btn"

    def test_match_elements_no_match(self, db_session, sample_project):
        """场景8: _match_elements() 无匹配 -> 步骤标记 _unmatched: True"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#submit-btn",
            text_content="Submit",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "完全不相关的文本XYZ", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0].get("_unmatched") is True

    def test_match_elements_selector_pattern(self, db_session, sample_project):
        """场景9: _match_elements() 选择器模式 -> 跳过匹配，直接使用"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#submit-btn",
            text_content="Submit",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "#my-custom-btn", "description": ""}]
        result = svc._match_elements(steps, [el])

        # 选择器模式的 target 保持不变
        assert result[0]["target"] == "#my-custom-btn"

    def test_match_elements_best_match(self, db_session, sample_project):
        """场景10: _match_elements() 多个 PageElement -> 使用最佳匹配"""
        el1 = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#btn1",
            text_content="提交",
            is_visible=1,
        )
        el2 = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#btn-login",
            text_content="登录按钮",
            is_visible=1,
        )
        db_session.add_all([el1, el2])
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "登录按钮", "description": ""}]
        result = svc._match_elements(steps, [el1, el2])

        # 应该匹配到 "登录按钮" 对应的 el2
        assert result[0]["target"] == "#btn-login"
        assert result[0]["_matched_selector"] == "#btn-login"

    def test_match_elements_empty_target(self, db_session, sample_project):
        """_match_elements() 空 target -> 跳过匹配直接追加"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#btn",
            text_content="Click",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "", "description": "no target"}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == ""

    def test_match_elements_by_placeholder(self, db_session, sample_project):
        """_match_elements() 通过 placeholder 字段匹配"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="input",
            selector="#email",
            placeholder="请输入邮箱",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "fill", "target": "请输入邮箱", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#email"

    def test_match_elements_by_name(self, db_session, sample_project):
        """_match_elements() 通过 name 字段匹配"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="input",
            selector="#username",
            name="username",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "fill", "target": "username", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#username"

    def test_match_elements_by_element_id(self, db_session, sample_project):
        """_match_elements() 通过 element_id 字段匹配"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="div",
            selector="#main-content",
            element_id="main-content",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "main-content", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#main-content"

    def test_match_elements_by_class_name(self, db_session, sample_project):
        """_match_elements() 通过 class_name 字段匹配"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector=".btn-primary",
            class_name="btn-primary",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "btn-primary", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == ".btn-primary"

    def test_match_elements_by_element_type(self, db_session, sample_project):
        """_match_elements() 通过 element_type 字段匹配"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="textarea",
            selector="#desc",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "fill", "target": "textarea", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#desc"

    def test_match_elements_empty_element_type_skipped(self, db_session, sample_project):
        """_match_elements() element_type 为空 -> 跳过该候选字段"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="",
            selector="#empty-type",
            text_content="Click Me",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "Click Me", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#empty-type"

    def test_match_elements_empty_candidate_skipped(self, db_session, sample_project):
        """_match_elements() 候选字段为空字符串 -> continue 跳过"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#only-type",
            text_content="",
            placeholder="",
            name="",
            element_id="",
            class_name="",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "button", "description": ""}]
        result = svc._match_elements(steps, [el])

        assert result[0]["target"] == "#only-type"

    def test_match_elements_clean_produces_empty(self, db_session, sample_project):
        """_match_elements() 清理后候选为空字符串 -> continue 跳过"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="span",
            selector="#icon-el",
            text_content="「」",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        steps = [{"action": "click", "target": "icon", "description": ""}]
        result = svc._match_elements(steps, [el])

        # 清理后 text_content 为空，但 element_type "span" 还能匹配
        assert result[0]["target"] == "#icon-el"


# ═══════════════════════════════════════════════
# _build_prompt() 测试
# ═══════════════════════════════════════════════

class TestBuildPrompt:
    """Prompt 模板渲染测试"""

    def test_build_prompt_placeholders_filled(self):
        """场景11: _build_prompt() 模板渲染 -> 所有占位符被填充"""
        prompt = _build_prompt(
            case_name="登录测试",
            pre_condition="用户已注册",
            expected_result="登录成功",
            steps_json='[{"action":"click"}]',
            elements_list="- [button] selector=#btn",
            target_url="https://example.com",
        )

        assert "登录测试" in prompt
        assert "用户已注册" in prompt
        assert "登录成功" in prompt
        assert '[{"action":"click"}]' in prompt
        assert "#btn" in prompt
        assert "https://example.com" in prompt

    def test_build_prompt_default_values(self):
        """_build_prompt() 使用默认值"""
        prompt = _build_prompt(
            case_name="Test",
            pre_condition="无",
            expected_result="无",
            steps_json="[]",
            elements_list="（无页面元素数据）",
        )

        assert "Test" in prompt
        assert "无" in prompt
        assert "[]" in prompt

    def test_build_prompt_fallback_template(self, mocker):
        """_build_prompt() prompt 文件不存在 -> 使用兜底模板"""
        mocker.patch("app.services.ai_service.os.path.exists", return_value=False)
        prompt = _build_prompt(
            case_name="FallbackTest",
            pre_condition="无",
            expected_result="无",
            steps_json="[]",
            elements_list="（无页面元素数据）",
        )

        assert "FallbackTest" in prompt
        assert "[]" in prompt
        assert "Playwright" in prompt


# ═══════════════════════════════════════════════
# _format_elements() 测试
# ═══════════════════════════════════════════════

class TestFormatElements:
    """元素列表格式化测试"""

    def test_format_elements_empty(self):
        """_format_elements() 空列表 -> 返回占位文本"""
        result = _format_elements([])
        assert result == "（无页面元素数据）"

    def test_format_elements_all_fields(self, db_session, sample_project):
        """_format_elements() 全字段元素 -> 所有字段出现在输出中"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="input",
            tag_name="input",
            element_id="user-email",
            name="email",
            class_name="form-control",
            selector="#email",
            text_content="",
            placeholder="请输入邮箱",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        result = _format_elements([el])

        assert "input" in result
        assert "id=user-email" in result
        assert "name=email" in result
        assert "class=form-control" in result
        assert 'placeholder="请输入邮箱"' in result
        assert "selector=#email" in result


# ═══════════════════════════════════════════════
# _call_openai() 测试
# ═══════════════════════════════════════════════

class TestCallOpenAI:
    """OpenAI 调用测试"""

    def test_call_openai_no_api_key_returns_mock(self):
        """场景12: _call_openai() 无 API Key -> 返回 mock 代码"""
        # OPENAI_API_KEY 在测试环境中已设为空字符串
        result = _call_openai("test prompt", "gpt-4o", target_url="https://example.com", steps_json="[]")
        assert "async def run_test" in result
        assert "page.goto" in result

    def test_call_openai_retry_success(self, mock_settings, mocker):
        """_call_openai() 前2次失败 → 第3次成功"""
        mock_settings("OPENAI_API_KEY", "test-key")
        import httpx

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.TimeoutException("Timeout")
            resp = mocker.MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": "async def run_test(page):\n    return {'success': True}"}}],
                "usage": {"total_tokens": 100},
            }
            return resp

        mock_client = mocker.MagicMock()
        mock_client.__enter__.return_value.post.side_effect = side_effect
        mocker.patch("app.services.ai_service.httpx.Client", return_value=mock_client)
        mocker.patch("app.services.ai_service.time.sleep")

        result = _call_openai("test prompt", "gpt-4o")
        assert "async def run_test" in result
        assert call_count[0] == 3

    def test_call_openai_retry_exhausted(self, mock_settings, mocker):
        """_call_openai() 3次全部失败 → 抛出 AIException"""
        mock_settings("OPENAI_API_KEY", "test-key")
        import httpx

        mock_client = mocker.MagicMock()
        mock_client.__enter__.return_value.post.side_effect = httpx.TimeoutException("Timeout")
        mocker.patch("app.services.ai_service.httpx.Client", return_value=mock_client)
        mocker.patch("app.services.ai_service.time.sleep")

        with pytest.raises(AIException, match="已重试3次"):
            _call_openai("test prompt", "gpt-4o")

    def test_circuit_breaker_raises_when_quota_exhausted(self, mock_settings):
        """每分钟调用达到上限 → 抛 AIException（熔断，防止烧 Token）"""
        from app.services.ai_service import ai_rate_limiter
        from app.config import settings
        mock_settings("OPENAI_API_KEY", "test-key")

        ai_rate_limiter._max_calls = 3  # 临时降低上限
        ai_rate_limiter._calls.clear()
        try:
            import time
            now = time.time()
            for _ in range(3):
                ai_rate_limiter._calls.append(now)

            with pytest.raises(AIException, match="熔断"):
                _call_openai("test prompt", "gpt-4o")
        finally:
            ai_rate_limiter._calls.clear()
            ai_rate_limiter._max_calls = settings.OPENAI_MAX_CALLS_PER_MIN

    def test_mock_mode_does_not_consume_quota(self):
        """Mock 模式（无 API Key）不消耗限流额度"""
        from app.services.ai_service import ai_rate_limiter
        ai_rate_limiter._calls.clear()
        try:
            result = _call_openai("test prompt", "gpt-4o")
            assert "async def run_test" in result
            assert len(ai_rate_limiter._calls) == 0
        finally:
            ai_rate_limiter._calls.clear()


class TestVisionCall:
    """_call_openai_vision() 测试 — 截图 + 文本多模态分析"""

    def test_vision_no_api_key_returns_empty(self):
        """无 API Key → 返回空字符串（Vision 不可用）"""
        result = _call_openai_vision("分析页面", b"fake_png")
        assert result == ""

    def test_vision_success(self, mock_settings, mocker):
        """正常调用 → 返回 LLM 内容"""
        mock_settings("OPENAI_API_KEY", "test-key")
        from app.services.ai_service import ai_rate_limiter
        ai_rate_limiter._calls.clear()

        resp = mocker.MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"need_action": false}'}}],
            "usage": {"total_tokens": 50},
        }
        mock_client = mocker.MagicMock()
        mock_client.__enter__.return_value.post.return_value = resp
        mocker.patch("app.services.ai_service.httpx.Client", return_value=mock_client)

        try:
            result = _call_openai_vision("分析页面", b"fake_png")
            assert result == '{"need_action": false}'
        finally:
            ai_rate_limiter._calls.clear()

    def test_vision_retry_then_success(self, mock_settings, mocker):
        """前2次失败 → 第3次成功"""
        mock_settings("OPENAI_API_KEY", "test-key")
        import httpx
        from app.services.ai_service import ai_rate_limiter
        ai_rate_limiter._calls.clear()

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.TimeoutException("Timeout")
            resp = mocker.MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": '{"need_action": true}'}}],
            }
            return resp

        mock_client = mocker.MagicMock()
        mock_client.__enter__.return_value.post.side_effect = side_effect
        mocker.patch("app.services.ai_service.httpx.Client", return_value=mock_client)
        mocker.patch("app.services.ai_service.time.sleep")

        try:
            result = _call_openai_vision("分析页面", b"fake_png", retries=3)
            assert result == '{"need_action": true}'
            assert call_count[0] == 3
        finally:
            ai_rate_limiter._calls.clear()

    def test_vision_all_failed_returns_empty(self, mock_settings, mocker):
        """全部重试失败 → 返回空字符串（不抛异常）"""
        mock_settings("OPENAI_API_KEY", "test-key")
        import httpx
        from app.services.ai_service import ai_rate_limiter
        ai_rate_limiter._calls.clear()

        mock_client = mocker.MagicMock()
        mock_client.__enter__.return_value.post.side_effect = httpx.TimeoutException("Timeout")
        mocker.patch("app.services.ai_service.httpx.Client", return_value=mock_client)
        mocker.patch("app.services.ai_service.time.sleep")

        try:
            result = _call_openai_vision("分析页面", b"fake_png")
            assert result == ""
        finally:
            ai_rate_limiter._calls.clear()

    def test_vision_circuit_breaker_returns_empty(self, mock_settings):
        """限流熔断 → 返回空字符串（跳过 Vision 分析）"""
        mock_settings("OPENAI_API_KEY", "test-key")
        from app.services.ai_service import ai_rate_limiter
        from app.config import settings
        import time

        ai_rate_limiter._max_calls = 2
        ai_rate_limiter._calls.clear()
        try:
            now = time.time()
            for _ in range(2):
                ai_rate_limiter._calls.append(now)
            assert _call_openai_vision("分析页面", b"fake_png") == ""
        finally:
            ai_rate_limiter._calls.clear()
            ai_rate_limiter._max_calls = settings.OPENAI_MAX_CALLS_PER_MIN


class TestRateLimiter:
    """AIRateLimiter 单元测试 — 滑动窗口熔断器"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        from app.services.ai_service import ai_rate_limiter
        from app.config import settings
        ai_rate_limiter._calls.clear()
        ai_rate_limiter._total_calls = 0
        yield
        ai_rate_limiter._calls.clear()
        ai_rate_limiter._total_calls = 0
        ai_rate_limiter._max_calls = settings.OPENAI_MAX_CALLS_PER_MIN

    def test_acquire_returns_true_below_limit(self):
        """未达上限 → acquire 返回 True，计数增加"""
        from app.services.ai_service import ai_rate_limiter
        assert ai_rate_limiter.acquire() is True
        assert ai_rate_limiter.recent_count == 1
        assert ai_rate_limiter.total_calls == 1

    def test_acquire_returns_false_when_full(self):
        """窗口已满 → acquire 返回 False（熔断）"""
        from app.services.ai_service import ai_rate_limiter
        ai_rate_limiter._max_calls = 2
        assert ai_rate_limiter.acquire() is True
        assert ai_rate_limiter.acquire() is True
        assert ai_rate_limiter.acquire() is False
        assert ai_rate_limiter.recent_count == 2

    def test_window_expiry_cleans_old_records(self, mocker):
        """超过 60 秒的记录被清理 → 重新获得额度"""
        from app.services.ai_service import ai_rate_limiter
        import time
        ai_rate_limiter._max_calls = 1

        fake_now = [time.time()]
        mocker.patch("app.utils.ai_rate_limiter.time.time", side_effect=lambda: fake_now[0])

        assert ai_rate_limiter.acquire() is True
        assert ai_rate_limiter.acquire() is False  # 窗口已满

        # 61 秒后，旧记录过期
        fake_now[0] += 61
        assert ai_rate_limiter.recent_count == 0
        assert ai_rate_limiter.acquire() is True

    def test_recent_count_cleans_expired(self, mocker):
        """recent_count 同样清理过期记录"""
        from app.services.ai_service import ai_rate_limiter
        import time

        fake_now = [time.time()]
        mocker.patch("app.utils.ai_rate_limiter.time.time", side_effect=lambda: fake_now[0])

        ai_rate_limiter._calls.append(fake_now[0] - 120)  # 过期记录
        assert ai_rate_limiter.recent_count == 0
        assert ai_rate_limiter.acquire() is True


# ═══════════════════════════════════════════════
# _extract_code() 测试
# ═══════════════════════════════════════════════

class TestExtractCode:
    """代码提取测试"""

    def test_extract_code_from_markdown_python(self):
        """场景13: _extract_code() 从 markdown 提取 -> ```python``` 标记被剥离"""
        raw = "```python\nasync def run_test(page):\n    return {'success': True}\n```"
        code = _extract_code(raw)
        assert code == "async def run_test(page):\n    return {'success': True}"
        assert "```" not in code

    def test_extract_code_from_markdown_plain(self):
        """_extract_code() 从无语言标记的 markdown 提取"""
        raw = "```\nasync def run_test(page):\n    return {'success': True}\n```"
        code = _extract_code(raw)
        assert code == "async def run_test(page):\n    return {'success': True}"

    def test_extract_code_plain(self):
        """场景14: _extract_code() 纯代码 -> 原样返回"""
        raw = "async def run_test(page):\n    return {'success': True}"
        code = _extract_code(raw)
        assert code == raw

    def test_extract_code_whitespace(self):
        """_extract_code() 前后空白被清理"""
        raw = "\n\n  async def run_test(page):\n    pass\n  "
        code = _extract_code(raw)
        assert code == "async def run_test(page):\n    pass"

    def test_extract_code_no_closing_fence(self):
        """_extract_code() 仅有 ```python 无闭合 ``` -> 返回标记后内容"""
        raw = "```python\nasync def run_test(page):\n    return {'success': True}"
        code = _extract_code(raw)
        assert "async def run_test" in code
        assert "```" not in code


# ═══════════════════════════════════════════════
# _validate_syntax() 测试
# ═══════════════════════════════════════════════

class TestValidateSyntax:
    """语法校验测试"""

    def test_validate_syntax_valid(self):
        """场景15: _validate_syntax() 有效代码 -> 无错误"""
        code = "async def run_test(page):\n    return {'success': True}"
        _validate_syntax(code)  # 不抛出异常即为通过

    def test_validate_syntax_error(self):
        """场景16: _validate_syntax() 语法错误 -> SyntaxError 抛出"""
        code = "def broken( {{{"
        with pytest.raises(SyntaxError):
            _validate_syntax(code)


# ═══════════════════════════════════════════════
# _security_check() 测试
# ═══════════════════════════════════════════════

class TestSecurityCheck:
    """安全检查测试"""

    def test_security_check_import_os(self):
        """场景17: _security_check() import os -> SecurityException 抛出"""
        code = "import os\nasync def run_test(page):\n    return {'success': True}"
        with pytest.raises(SecurityException):
            _security_check(code)

    def test_security_check_eval(self):
        """场景18: _security_check() eval() -> SecurityException 抛出"""
        code = "async def run_test(page):\n    eval('1+1')\n    return {'success': True}"
        with pytest.raises(SecurityException):
            _security_check(code)

    def test_security_check_valid(self):
        """场景19: _security_check() 有效代码 -> 无错误"""
        code = "async def run_test(page):\n    return {'success': True, 'steps': []}"
        _security_check(code)  # 不抛出异常即为通过

    def test_security_check_import_sys(self):
        """_security_check() import sys -> SecurityException"""
        code = "import sys\nasync def run_test(page):\n    return {'success': True}"
        with pytest.raises(SecurityException):
            _security_check(code)

    def test_security_check_exec(self):
        """_security_check() exec() -> SecurityException"""
        code = "async def run_test(page):\n    exec('x=1')\n    return {'success': True}"
        with pytest.raises(SecurityException):
            _security_check(code)

    def test_security_check_syntax_error_skips(self):
        """_security_check() 语法错误 -> 跳过检查不报错"""
        code = "this is not valid python {{{"
        _security_check(code)  # 语法错误时安全模块静默返回

    def test_security_check_import_from_os(self):
        """_security_check() from os import path -> SecurityException"""
        code = "from os import path\nasync def run_test(page):\n    return {'success': True}"
        with pytest.raises(SecurityException):
            _security_check(code)

    def test_security_check_import_from_subprocess(self):
        """_security_check() from subprocess import run -> SecurityException"""
        code = "from subprocess import run\nasync def run_test(page):\n    return {'success': True}"
        with pytest.raises(SecurityException):
            _security_check(code)

    def test_security_check_relative_import_skipped(self):
        """_security_check() 相对导入 (from . import x) -> module=None，跳过检查"""
        code = "from . import utils\nasync def run_test(page):\n    return {'success': True}"
        _security_check(code)  # 不抛出异常，相对导入 module 为 None


# ═══════════════════════════════════════════════
# generate_batch() 测试
# ═══════════════════════════════════════════════

class TestGenerateBatch:
    """批量生成测试"""

    def test_batch_partial_failure(self, db_session, sample_project):
        """场景20: generate_batch() 3 个用例，1 个失败 -> 2 success, 1 failed"""
        from app.models.test_case import TestCase

        # 创建 3 个用例
        case1 = TestCase(
            project_id=sample_project.id,
            case_name="Case 1",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "Open"},
            ]),
            status="pending",
        )
        case2 = TestCase(
            project_id=sample_project.id,
            case_name="Case 2",
            steps=json.dumps([
                {"step_number": 1, "action": "click", "target": "#btn", "value": "", "description": "Click"},
            ]),
            status="pending",
        )
        case3 = TestCase(
            project_id=sample_project.id,
            case_name="Case 3",
            steps=json.dumps([]),  # 无步骤，会失败
            status="pending",
        )
        db_session.add_all([case1, case2, case3])
        db_session.commit()
        db_session.refresh(case1)
        db_session.refresh(case2)
        db_session.refresh(case3)

        svc = AIService(db_session)
        results = svc.generate_batch(sample_project.id, [case1.id, case2.id, case3.id])

        assert len(results) == 3
        success_count = sum(1 for r in results if r["status"] == "success")
        failed_count = sum(1 for r in results if r["status"] == "failed")
        assert success_count == 2
        assert failed_count == 1

    def test_batch_empty_list(self, db_session, sample_project):
        """generate_batch() 空 case_ids -> 返回空列表"""
        svc = AIService(db_session)
        results = svc.generate_batch(sample_project.id, [])
        assert results == []

    def test_batch_all_success(self, db_session, sample_project, mock_llm):
        """generate_batch() 全部成功 -> 所有状态为 success"""
        from app.models.test_case import TestCase

        case1 = TestCase(
            project_id=sample_project.id,
            case_name="Batch Case 1",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "Open"},
            ]),
            status="pending",
        )
        case2 = TestCase(
            project_id=sample_project.id,
            case_name="Batch Case 2",
            steps=json.dumps([
                {"step_number": 1, "action": "click", "target": "#btn", "value": "", "description": "Click"},
            ]),
            status="pending",
        )
        db_session.add_all([case1, case2])
        db_session.commit()
        db_session.refresh(case1)
        db_session.refresh(case2)

        svc = AIService(db_session)
        results = svc.generate_batch(sample_project.id, [case1.id, case2.id])

        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)


# ═══════════════════════════════════════════════
# generate_batch_sync() 测试
# ═══════════════════════════════════════════════

class TestGenerateBatchSync:
    """批量生成（同步）测试"""

    def test_generate_batch_sync(self, db_session, sample_project, mock_llm):
        """generate_batch_sync() 批量同步生成 -> 更新 batch_job 状态"""
        from app.models.test_case import TestCase

        case = TestCase(
            project_id=sample_project.id,
            case_name="Sync Case",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "Open"},
            ]),
            status="pending",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        svc = AIService(db_session)
        job = BatchJob(batch_id="test-batch-1", total=1)
        svc.generate_batch_sync(sample_project.id, [case.id], job)

        assert job.status == "completed"
        assert job.completed == 1
        assert job.failed == 0

    def test_generate_batch_sync_with_failure(self, db_session, sample_project):
        """generate_batch_sync() 有失败用例 -> failed 计数增加"""
        from app.models.test_case import TestCase

        case = TestCase(
            project_id=sample_project.id,
            case_name="Fail Case",
            steps=json.dumps([]),  # 无步骤，会失败
            status="pending",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        svc = AIService(db_session)
        job = BatchJob(batch_id="test-batch-2", total=1)
        svc.generate_batch_sync(sample_project.id, [case.id], job)

        assert job.status == "completed"
        assert job.failed == 1


# ═══════════════════════════════════════════════
# get_latest_code() 测试
# ═══════════════════════════════════════════════

class TestGetLatestCode:
    """获取最新代码测试"""

    def test_get_latest_code_with_data(self, db_session, sample_project, sample_test_case, sample_generated_code):
        """场景21: get_latest_code() 有生成代码 -> 返回 code dict"""
        svc = AIService(db_session)
        result = svc.get_latest_code(sample_project.id, sample_test_case.id)

        assert isinstance(result, dict)
        assert result["code_id"] == sample_generated_code.id
        assert "code_content" in result
        assert result["is_valid"] is True

    def test_get_latest_code_no_code(self, db_session, sample_project):
        """场景22: get_latest_code() 无代码 -> 抛出 AIException"""
        from app.models.test_case import TestCase
        case = TestCase(
            project_id=sample_project.id,
            case_name="No Code Case",
            steps=json.dumps([
                {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": ""},
            ]),
            status="pending",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        svc = AIService(db_session)
        with pytest.raises(AIException, match="尚无生成的代码"):
            svc.get_latest_code(sample_project.id, case.id)

    def test_get_latest_code_nonexistent_case(self, db_session, sample_project):
        """get_latest_code() 不存在用例 -> 抛出 AIException"""
        svc = AIService(db_session)
        with pytest.raises(AIException, match="用例 99999 不存在"):
            svc.get_latest_code(sample_project.id, 99999)


# ═══════════════════════════════════════════════
# Mock 模式代码结构测试
# ═══════════════════════════════════════════════

class TestMockCode:
    """Mock 代码结构测试"""

    def test_mock_code_structure(self):
        """场景23: Mock 模式代码结构 -> 包含 async def run_test, page.goto"""
        code = _mock_code(target_url="https://example.com", steps_json="[]")
        assert "async def run_test" in code
        assert "page.goto" in code
        assert "from playwright.async_api import Page" in code
        assert '"success"' in code

    def test_mock_code_with_steps(self):
        """_mock_code() 包含步骤时生成对应代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "fill", "target": "#username", "value": "admin", "description": "输入用户名"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "#username" in code
        assert "fill" in code
        assert "admin" in code

    def test_mock_code_click_without_target(self):
        """_mock_code() click 无 target -> 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "click", "target": "", "value": "", "description": "点击"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "无 selector" in code

    def test_mock_code_select_without_target(self):
        """_mock_code() select 无 target -> 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "select", "target": "", "value": "option1", "description": "选择"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "无 selector" in code

    def test_mock_code_wait_action(self):
        """_mock_code() wait 动作 -> 生成 wait_for_timeout 代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "wait", "target": "", "value": "2000", "description": "等待"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "wait_for_timeout" in code
        assert "2000" in code

    def test_mock_code_screenshot_action(self):
        """_mock_code() screenshot 动作 -> 生成截图代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "screenshot", "target": "", "value": "", "description": "截图"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "screenshot" in code

    def test_mock_code_unrecognized_action(self):
        """_mock_code() 未识别动作 -> 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "unknown_action", "target": "", "value": "", "description": "未知"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "未识别" in code

    def test_mock_code_invalid_json(self):
        """_mock_code() 无效 JSON -> 静默回退，生成空步骤代码"""
        code = _mock_code(target_url="https://example.com", steps_json="not valid json {{{")
        assert "async def run_test" in code
        assert "page.goto" in code

    def test_mock_code_fill_without_target(self):
        """_mock_code() fill 无 target -> 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "fill", "target": "", "value": "admin", "description": "填充"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "无 selector" in code

    def test_mock_code_select_with_target(self):
        """_mock_code() select 有 target -> 生成 select_option 代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "select", "target": "#dropdown", "value": "option1", "description": "选择"},
        ])
        code = _mock_code(target_url="https://example.com", steps_json=steps)
        assert "select_option" in code
        assert "#dropdown" in code


class TestMockAndroidCode:
    """Android Mock 代码结构测试（_mock_android_code）"""

    def test_platform_android_dispatches_to_android(self):
        """_mock_code() platform=android → 生成同步 run_test(driver) 代码"""
        code = _mock_code(target_url="https://example.com", steps_json="[]", platform="android")
        assert "def run_test(driver)" in code
        assert "async" not in code
        assert "AppiumBy" not in code  # 注入运行时不导入 AppiumBy

    def test_mock_android_structure(self):
        """基础结构：同步函数 + steps_result"""
        code = _mock_android_code(steps_json="[]")
        assert "def run_test(driver)" in code
        assert "steps_result = []" in code
        assert "from datetime import datetime" in code

    def test_click_with_target(self):
        """click 有 target → 生成 find_element + click 代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "click", "target": "//button[@id='btn']", "value": "", "description": "点击"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "点击" in code
        assert "find_element" in code
        assert "click" in code

    def test_click_without_target(self):
        """click 无 target → 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "click", "target": "", "value": "", "description": "点击"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "无 selector" in code

    def test_fill_with_target(self):
        """fill 有 target → 生成 send_keys 代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "fill", "target": "//input[@id='user']", "value": "admin", "description": "输入"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "send_keys" in code
        assert "admin" in code

    def test_fill_without_target(self):
        """fill 无 target → 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "fill", "target": "", "value": "admin", "description": "输入"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "无 selector" in code

    def test_wait_with_numeric_value(self):
        """wait 数字 value → 生成 time.sleep(2.0)"""
        steps = json.dumps([
            {"step_number": 1, "action": "wait", "target": "", "value": "2000", "description": "等待"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "time.sleep(2.0)" in code

    def test_wait_with_non_numeric_value(self):
        """wait 非数字 value → 回退默认 1000ms"""
        steps = json.dumps([
            {"step_number": 1, "action": "wait", "target": "", "value": "abc", "description": "等待"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "time.sleep(1.0)" in code

    def test_back_action(self):
        """back 动作 → 生成 driver.back()"""
        steps = json.dumps([
            {"step_number": 1, "action": "back", "target": "", "value": "", "description": "返回"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "driver.back()" in code

    def test_screenshot_action(self):
        """screenshot 动作 → 生成 save_screenshot"""
        steps = json.dumps([
            {"step_number": 1, "action": "screenshot", "target": "", "value": "", "description": "截图"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "save_screenshot" in code

    def test_unrecognized_action(self):
        """未识别动作 → 生成跳过代码"""
        steps = json.dumps([
            {"step_number": 1, "action": "tap", "target": "", "value": "", "description": "未知"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "未识别" in code

    def test_invalid_json(self):
        """无效 JSON → 静默回退，生成空步骤代码"""
        code = _mock_android_code(steps_json="not valid {{{")
        assert "def run_test(driver)" in code

    def test_special_chars_escaped(self):
        """target 含引号/反斜杠 → 正确转义"""
        steps = json.dumps([
            {"step_number": 1, "action": "click", "target": "//div[@title='it\\'s']", "value": "", "description": "点击"},
        ])
        code = _mock_android_code(steps_json=steps)
        assert "\\\\'" in code or "\\'" in code


# ═══════════════════════════════════════════════
# elements 匹配集成测试
# ═══════════════════════════════════════════════

class TestElementMatchingIntegration:
    """元素匹配 + 生成集成测试"""

    def test_match_elements_in_generate_single(self, db_session, sample_project, sample_test_case, mock_llm):
        """元素匹配在 generate_single 流程中正确工作"""
        el = PageElement(
            project_id=sample_project.id,
            element_type="button",
            selector="#login-btn",
            text_content="登录",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()

        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)

        assert result.is_valid is True
        assert result.code_id > 0