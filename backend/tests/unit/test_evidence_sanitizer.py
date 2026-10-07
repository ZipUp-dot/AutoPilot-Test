"""AC-01：Evidence Sanitizer —— 7 类敏感值必须先于 AI Prompt 脱敏

EXT-AITC-10A 节拍 1（RED 先行）。目标模块 app/utils/evidence_sanitizer.py 尚未实现。
"""

from app.utils.evidence_sanitizer import PLACEHOLDER, sanitize_elements

_RAW = {
    "email": "alice@example.com",
    "phone": "13800138000",
    "token": "tok_live_9f8e7d6c5b4a",
    "api_key": "sk-abcdef0123456789",
    "cookie": "sessionid=abc123xyz",
    "session_id": "sess-778899aabb",
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
}


def _elements() -> list[dict]:
    return [
        {"selector": "#email", "text_content": _RAW["email"]},
        {"selector": "#phone", "text_content": _RAW["phone"]},
        {"selector": "#tok", "attrs": {"data-token": _RAW["token"]}},
        {"selector": "#key", "attrs": {"data-api-key": _RAW["api_key"]}},
        {"selector": "#ck", "attrs": {"cookie": _RAW["cookie"]}},
        {"selector": "#sid", "attrs": {"session_id": _RAW["session_id"]}},
        {"selector": "#auth", "attrs": {"authorization": _RAW["authorization"]}},
    ]


def test_seven_sensitive_classes_are_redacted():
    """7 类敏感值（email/phone/token/api key/cookie/session id/authorization）全部脱敏"""
    blob = repr(sanitize_elements(_elements()))
    leaked = [kind for kind, raw in _RAW.items() if raw in blob]
    assert leaked == [], f"以下敏感类别未脱敏: {leaked}"


def test_placeholder_present_and_shape_preserved():
    """脱敏后结构不塌陷：条目数、定位符保持；占位符可见"""
    out = sanitize_elements(_elements())
    assert len(out) == len(_elements())
    assert all(el["selector"].startswith("#") for el in out)
    assert PLACEHOLDER in repr(out)


def test_input_not_mutated_in_place():
    """脱敏不得就地改写入参（原始证据不可被污染）"""
    src = _elements()
    sanitize_elements(src)
    assert src[0]["text_content"] == _RAW["email"]


def test_prompt_after_sanitize_contains_no_raw_secret():
    """8.3：Sanitization 必须先于 AI Prompt —— 送入 Prompt 的文本不得含原文"""
    prompt = "页面元素证据：\n" + repr(sanitize_elements(_elements()))
    leaked = [raw for raw in _RAW.values() if raw in prompt]
    assert leaked == [], f"Prompt 中泄漏原文: {leaked}"
