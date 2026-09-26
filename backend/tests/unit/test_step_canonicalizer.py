"""StepCanonicalizer 测试 — canonicalize_steps / hash_steps 唯一入口"""

import json

from app.utils.step_canonicalizer import canonicalize_steps, hash_steps


class TestCanonicalizeSteps:
    """canonicalize_steps() — 仅规范 JSON 表达"""

    def test_same_steps_different_key_order_same_canonical(self):
        a = [{"step_number": 1, "action": "click", "target": "#btn",
              "value": "", "description": "go"}]
        b = [{"description": "go", "target": "#btn", "value": "",
              "action": "click", "step_number": 1}]
        # dict 键顺序不同 → 相同 canonical
        assert canonicalize_steps(a) == canonicalize_steps(b)

    def test_same_steps_whitespace_irrelevant(self):
        # 语义相同、仅 JSON 结构空白不同 → 相同 canonical
        a = [{"step_number": 1, "action": "click", "target": "#b"}]
        b = '[\n  {"step_number": 1, "action": "click", "target": "#b"}\n]'
        assert canonicalize_steps(a) == canonicalize_steps(json.loads(b))

    def test_business_string_content_preserved(self):
        # 业务字符串里的空格（如 input_value）不 strip
        a = [{"action": "fill", "value": "a  b"}]
        b = [{"action": "fill", "value": "a b"}]
        assert canonicalize_steps(a) != canonicalize_steps(b)

    def test_list_order_preserved(self):
        # List / Step 顺序有业务意义，必须保留（不排序）
        a = [{"step_number": 1}, {"step_number": 2}]
        b = [{"step_number": 2}, {"step_number": 1}]
        assert canonicalize_steps(a) != canonicalize_steps(b)


class TestHashSteps:
    """hash_steps() — 内部仅经 canonicalize_steps 再 SHA-256"""

    def test_returns_sha256_hex_length(self):
        h = hash_steps([{"step_number": 1, "action": "click"}])
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_steps_same_hash(self):
        a = [{"step_number": 1, "action": "click", "target": "#b"}]
        b = [{"target": "#b", "action": "click", "step_number": 1}]
        assert hash_steps(a) == hash_steps(b)

    def test_semantically_different_different_hash(self):
        a = [{"step_number": 1, "action": "click", "target": "#a"}]
        b = [{"step_number": 1, "action": "click", "target": "#b"}]
        assert hash_steps(a) != hash_steps(b)