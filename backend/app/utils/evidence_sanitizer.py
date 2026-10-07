"""Evidence Sanitizer —— 证据脱敏（必须在 AI Prompt 之前执行，8.3）

七类敏感值 + 通用敏感键名表。输入/输出均为元素 JSON（list[dict]），
不就地修改入参（原始证据不可被污染）。

脱敏语义：
  - 键名命中敏感词根（归一化后含 token / apikey / authorization / cookie /
    sessionid / password / secret / email / phone …）→ 整值替换为占位符；
  - 其余字符串值 → 按值形态正则替换（email / phone / token / api key /
    cookie / session id / authorization）。
"""

import copy
import re

PLACEHOLDER = "[REDACTED]"

# ── 敏感键名词根（归一化后子串匹配）──
_SENSITIVE_KEY_STEMS = (
    "token", "apikey", "authorization", "cookie", "sessionid", "session",
    "password", "passwd", "pwd", "secret", "credential",
    "email", "phone", "mobile", "telephone",
)

# ── 值形态规则（七类）──
_VALUE_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    "token": re.compile(r"(?i)\btok(?:en)?[_-][A-Za-z0-9._-]{6,}"),
    "api_key": re.compile(r"(?i)\b(?:sk|api)[_-][A-Za-z0-9._-]{8,}"),
    "cookie": re.compile(r"(?i)\b(?:sessionid|jsessionid|sid|session)\s*=\s*[^;\s]+"),
    "session_id": re.compile(r"(?i)\bsess(?:ion)?[_-][A-Za-z0-9-]{6,}"),
    "authorization": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
}


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: str) -> bool:
    """键名是否属敏感字段（归一化后含敏感词根）"""
    norm = _norm_key(key)
    return any(stem in norm for stem in _SENSITIVE_KEY_STEMS)


def scrub_text(value: str) -> str:
    """按值形态替换 7 类敏感值"""
    out = value
    for pattern in _VALUE_PATTERNS.values():
        out = pattern.sub(PLACEHOLDER, out)
    return out


def _scrub(node):
    if isinstance(node, dict):
        return {
            k: (PLACEHOLDER if is_sensitive_key(k) else _scrub(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_scrub(item) for item in node]
    if isinstance(node, str):
        return scrub_text(node)
    return node


def sanitize_elements(elements: list[dict]) -> list[dict]:
    """脱敏元素集：返回新对象，绝对不就地改写入参"""
    return [_scrub(copy.deepcopy(el)) for el in (elements or [])]
