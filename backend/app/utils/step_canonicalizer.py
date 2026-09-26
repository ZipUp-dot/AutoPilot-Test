"""步骤标准化 — 单一 hash 计算入口

只规范 JSON 表达（字段顺序 / 结构空白），不规范化业务语义：

  - 不 strip 业务字符串内容：input_value / description 里的空格可能是业务数据
  - dict 键按 sort_keys 排序（不同 JSON 字段书写顺序 → 相同 canonical）
  - List / Step 顺序必须保留（步骤顺序有业务意义）
  - 非字符串标量（如 int step_number）原样保留，不做类型推断合并

本模块仅暴露两个入口：canonicalize_steps / hash_steps，禁止在此之外手写 hash。
"""

import hashlib
import json


def canonicalize_steps(steps) -> str:
    """标准化字段顺序 / JSON 结构空白后的 canonical JSON 字符串。

    Args:
        steps: TestCase.steps 解析后的 Python 对象（list[dict] 等）

    Returns:
        canonical JSON 字符串（紧凑、无多余空白、dict 键已排序）
    """
    return json.dumps(
        steps,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def hash_steps(steps) -> str:
    """对步骤进行 SHA-256 哈希（内部仅调用 canonicalize_steps）。

    Args:
        steps: 步骤对象

    Returns:
        64 位十六进制 SHA-256
    """
    canonical = canonicalize_steps(steps)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()