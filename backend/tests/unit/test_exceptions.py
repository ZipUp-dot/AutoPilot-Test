"""Test exception classes and global handlers from app/exceptions.py"""

import pytest
from unittest.mock import MagicMock

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions import (
    AppException,
    NotFoundException,
    ValidationException,
    AIException,
    PlaywrightException,
    SecurityException,
    app_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)


# ── Fixtures ──

@pytest.fixture
def mock_request():
    return MagicMock(spec=Request)


# ═══════════════════════════════════════════════
# AppException base class
# ═══════════════════════════════════════════════

class TestAppException:
    def test_defaults(self):
        exc = AppException()
        assert exc.code == -1
        assert exc.message == "error"
        assert exc.status_code == 400

    def test_custom_code(self):
        exc = AppException(code=1001)
        assert exc.code == 1001
        assert exc.message == "error"
        assert exc.status_code == 400

    def test_custom_message(self):
        exc = AppException(message="自定义错误")
        assert exc.code == -1
        assert exc.message == "自定义错误"
        assert exc.status_code == 400

    def test_custom_status_code(self):
        exc = AppException(status_code=418)
        assert exc.code == -1
        assert exc.message == "error"
        assert exc.status_code == 418

    def test_all_custom(self):
        exc = AppException(code=2001, message="全部自定义", status_code=503)
        assert exc.code == 2001
        assert exc.message == "全部自定义"
        assert exc.status_code == 503

    def test_is_instance_of_exception(self):
        exc = AppException()
        assert isinstance(exc, Exception)


# ═══════════════════════════════════════════════
# NotFoundException
# ═══════════════════════════════════════════════

class TestNotFoundException:
    def test_defaults(self):
        exc = NotFoundException()
        assert exc.code == 404
        assert exc.message == "资源不存在"
        assert exc.status_code == 404

    def test_custom_message(self):
        exc = NotFoundException(message="项目 42 不存在")
        assert exc.code == 404
        assert exc.message == "项目 42 不存在"
        assert exc.status_code == 404

    def test_is_subclass_of_app_exception(self):
        assert issubclass(NotFoundException, AppException)


# ═══════════════════════════════════════════════
# ValidationException
# ═══════════════════════════════════════════════

class TestValidationException:
    def test_defaults(self):
        exc = ValidationException()
        assert exc.code == 422
        assert exc.message == "参数校验失败"
        assert exc.status_code == 422

    def test_custom_message(self):
        exc = ValidationException(message="name 字段不能为空")
        assert exc.code == 422
        assert exc.message == "name 字段不能为空"
        assert exc.status_code == 422

    def test_is_subclass_of_app_exception(self):
        assert issubclass(ValidationException, AppException)


# ═══════════════════════════════════════════════
# AIException
# ═══════════════════════════════════════════════

class TestAIException:
    def test_defaults(self):
        exc = AIException()
        assert exc.code == 500
        assert exc.message == "AI 服务异常"
        assert exc.status_code == 500

    def test_custom_message(self):
        exc = AIException(message="OpenAI API 超时")
        assert exc.code == 500
        assert exc.message == "OpenAI API 超时"
        assert exc.status_code == 500

    def test_is_subclass_of_app_exception(self):
        assert issubclass(AIException, AppException)


# ═══════════════════════════════════════════════
# PlaywrightException
# ═══════════════════════════════════════════════

class TestPlaywrightException:
    def test_defaults(self):
        exc = PlaywrightException()
        assert exc.code == 500
        assert exc.message == "浏览器操作异常"
        assert exc.status_code == 500

    def test_custom_message(self):
        exc = PlaywrightException(message="页面加载超时")
        assert exc.code == 500
        assert exc.message == "页面加载超时"
        assert exc.status_code == 500

    def test_is_subclass_of_app_exception(self):
        assert issubclass(PlaywrightException, AppException)


# ═══════════════════════════════════════════════
# SecurityException
# ═══════════════════════════════════════════════

class TestSecurityException:
    def test_defaults(self):
        exc = SecurityException()
        assert exc.code == 403
        assert exc.message == "安全校验失败"
        assert exc.status_code == 403

    def test_custom_message(self):
        exc = SecurityException(message="检测到危险代码: os.system")
        assert exc.code == 403
        assert exc.message == "检测到危险代码: os.system"
        assert exc.status_code == 403

    def test_is_subclass_of_app_exception(self):
        assert issubclass(SecurityException, AppException)


# ═══════════════════════════════════════════════
# app_exception_handler
# ═══════════════════════════════════════════════

class TestAppExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_json_response_with_correct_format(self, mock_request):
        exc = AppException(code=1001, message="测试错误", status_code=400)
        response = await app_exception_handler(mock_request, exc)
        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        body = response.body.decode() if isinstance(response.body, bytes) else response.body
        import json
        data = json.loads(body)
        assert data == {"code": 1001, "message": "测试错误", "data": None}

    @pytest.mark.asyncio
    async def test_with_not_found_exception(self, mock_request):
        exc = NotFoundException(message="项目 1 不存在")
        response = await app_exception_handler(mock_request, exc)
        assert response.status_code == 404
        import json
        data = json.loads(response.body)
        assert data == {"code": 404, "message": "项目 1 不存在", "data": None}

    @pytest.mark.asyncio
    async def test_with_ai_exception(self, mock_request):
        exc = AIException(message="模型调用失败")
        response = await app_exception_handler(mock_request, exc)
        assert response.status_code == 500
        import json
        data = json.loads(response.body)
        assert data == {"code": 500, "message": "模型调用失败", "data": None}


# ═══════════════════════════════════════════════
# http_exception_handler
# ═══════════════════════════════════════════════

class TestHttpExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_json_response_with_correct_format(self, mock_request):
        exc = StarletteHTTPException(status_code=404, detail="Not Found")
        response = await http_exception_handler(mock_request, exc)
        assert isinstance(response, JSONResponse)
        assert response.status_code == 404
        import json
        data = json.loads(response.body)
        assert data == {"code": 404, "message": "Not Found", "data": None}

    @pytest.mark.asyncio
    async def test_with_500_status(self, mock_request):
        exc = StarletteHTTPException(status_code=500, detail="Internal Server Error")
        response = await http_exception_handler(mock_request, exc)
        assert response.status_code == 500
        import json
        data = json.loads(response.body)
        assert data == {"code": 500, "message": "Internal Server Error", "data": None}

    @pytest.mark.asyncio
    async def test_with_403_status(self, mock_request):
        exc = StarletteHTTPException(status_code=403, detail="Forbidden")
        response = await http_exception_handler(mock_request, exc)
        assert response.status_code == 403
        import json
        data = json.loads(response.body)
        assert data == {"code": 403, "message": "Forbidden", "data": None}


# ═══════════════════════════════════════════════
# validation_exception_handler
# ═══════════════════════════════════════════════

class TestValidationExceptionHandler:
    @pytest.mark.asyncio
    async def test_single_field_error(self, mock_request):
        exc = RequestValidationError(errors=[{
            "loc": ["body", "name"],
            "msg": "field required",
            "type": "value_error.missing",
        }])
        response = await validation_exception_handler(mock_request, exc)
        assert response.status_code == 422
        import json
        data = json.loads(response.body)
        assert data["code"] == 422
        assert data["data"] is None
        assert "body -> name: field required" in data["message"]

    @pytest.mark.asyncio
    async def test_multiple_field_errors_aggregated_with_semicolons(self, mock_request):
        exc = RequestValidationError(errors=[
            {"loc": ["body", "name"], "msg": "field required", "type": "value_error.missing"},
            {"loc": ["body", "page"], "msg": "ensure this value is greater than 0", "type": "type_error"},
            {"loc": ["query", "size"], "msg": "value is not a valid integer", "type": "type_error.integer"},
        ])
        response = await validation_exception_handler(mock_request, exc)
        assert response.status_code == 422
        import json
        data = json.loads(response.body)
        assert data["code"] == 422
        assert data["data"] is None
        message = data["message"]
        assert "body -> name: field required" in message
        assert "body -> page: ensure this value is greater than 0" in message
        assert "query -> size: value is not a valid integer" in message
        # 三个错误应该用 "; " 连接
        assert message.count("; ") == 2

    @pytest.mark.asyncio
    async def test_nested_loc_path(self, mock_request):
        exc = RequestValidationError(errors=[{
            "loc": ["body", "items", 0, "name"],
            "msg": "field required",
            "type": "value_error.missing",
        }])
        response = await validation_exception_handler(mock_request, exc)
        import json
        data = json.loads(response.body)
        assert "body -> items -> 0 -> name: field required" in data["message"]

    @pytest.mark.asyncio
    async def test_empty_errors(self, mock_request):
        exc = RequestValidationError(errors=[])
        response = await validation_exception_handler(mock_request, exc)
        assert response.status_code == 422
        import json
        data = json.loads(response.body)
        assert data["code"] == 422
        assert data["message"] == ""
        assert data["data"] is None


# ═══════════════════════════════════════════════
# general_exception_handler
# ═══════════════════════════════════════════════

class TestGeneralExceptionHandler:
    @pytest.mark.asyncio
    async def test_wraps_message_with_prefix(self, mock_request):
        exc = ValueError("Something went wrong")
        response = await general_exception_handler(mock_request, exc)
        assert isinstance(response, JSONResponse)
        assert response.status_code == 500
        import json
        data = json.loads(response.body)
        assert data == {"code": 500, "message": "服务器内部错误: Something went wrong", "data": None}

    @pytest.mark.asyncio
    async def test_with_runtime_error(self, mock_request):
        exc = RuntimeError("Unexpected state")
        response = await general_exception_handler(mock_request, exc)
        assert response.status_code == 500
        import json
        data = json.loads(response.body)
        assert data == {"code": 500, "message": "服务器内部错误: Unexpected state", "data": None}

    @pytest.mark.asyncio
    async def test_with_empty_message_exception(self, mock_request):
        exc = Exception()
        response = await general_exception_handler(mock_request, exc)
        assert response.status_code == 500
        import json
        data = json.loads(response.body)
        assert data == {"code": 500, "message": "服务器内部错误: ", "data": None}