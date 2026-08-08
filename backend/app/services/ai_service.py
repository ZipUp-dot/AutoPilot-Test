"""AI 代码生成服务 — 元素匹配 + Prompt 构建 + OpenAI 调用 + 安全校验"""

import ast
import difflib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.test_case import TestCase
from app.models.generated_code import GeneratedCode
from app.models.element import PageElement
from app.exceptions import AIException, SecurityException

logger = logging.getLogger("autopilot.ai")

# ── 危险导入黑名单 ──
BANNED_IMPORTS = frozenset({
    "os", "sys", "subprocess", "socket", "requests",
    "urllib", "ftplib", "smtplib", "shutil", "signal",
    "ctypes", "multiprocessing", "threading._",
})

# ── 危险内置函数黑名单 ──
BANNED_BUILTINS = frozenset({
    "eval", "exec", "open", "compile", "__import__",
    "getattr.__", "setattr.__", "delattr.__",
})

# ── 选择器特征 ──
SELECTOR_PATTERN = re.compile(r'[#\.\[/\(]')


@dataclass
class GenerateResult:
    """代码生成结果"""
    code_id: Optional[int] = None
    code_content: str = ""
    is_valid: bool = False
    syntax_error: Optional[str] = None
    ai_model: Optional[str] = None


@dataclass
class BatchJob:
    """批量生成任务"""
    batch_id: str
    status: str = "running"  # running / completed / failed
    total: int = 0
    completed: int = 0
    failed: int = 0


class AIService:
    """AI 代码生成服务

    - 元素智能匹配（difflib.SequenceMatcher）
    - Prompt 模板热更新（每次调用时读取文件）
    - OpenAI API 调用 + 3 次指数退避重试
    - 代码提取（去 markdown 标记）
    - 语法校验（ast.parse）
    - 安全检查（导入/内置函数黑名单）
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 单条生成
    # ═══════════════════════════════════════════════

    def generate_single(self, project_id: int, case_id: int) -> GenerateResult:
        """为单条用例生成 Playwright 代码

        流程:
          1. 查用例 → 取 steps
          2. 查元素 → 智能匹配
          3. 读 Prompt 模板 → 构建 prompt
          4. 调 LLM → 提取代码
          5. 语法校验 + 安全检查
          6. 写入 generated_codes → 更新用例状态
        """
        case = (
            self._db.query(TestCase)
            .filter(TestCase.id == case_id, TestCase.project_id == project_id)
            .first()
        )
        if not case:
            raise AIException(f"用例 {case_id} 不存在")

        steps = json.loads(case.steps) if case.steps else []
        if not steps:
            raise AIException("用例无步骤数据")

        # 1. 查元素 + 智能匹配
        elements = (
            self._db.query(PageElement)
            .filter(PageElement.project_id == project_id, PageElement.is_visible == 1)
            .all()
        )

        matched_steps = self._match_elements(steps, elements)

        # 2. 构建 Prompt
        elements_list = _format_elements(elements)
        prompt = _build_prompt(
            case_name=case.case_name,
            pre_condition=case.pre_condition or "无",
            expected_result=case.expected_result or "无",
            steps_json=json.dumps(matched_steps, ensure_ascii=False, indent=2),
            elements_list=elements_list,
        )

        # 3. 调用 LLM
        try:
            raw_code = _call_openai(prompt, settings.OPENAI_MODEL)
        except Exception as e:
            raise AIException(f"AI 服务调用失败: {str(e)}")

        # 4. 提取代码
        code = _extract_code(raw_code)

        # 5. 校验
        syntax_error = None
        is_valid = 1
        try:
            _validate_syntax(code)
            _security_check(code)
        except SyntaxError as e:
            syntax_error = str(e)
            is_valid = 0
        except SecurityException as e:
            syntax_error = str(e.message)
            is_valid = 0

        # 6. 存储
        gen_code = GeneratedCode(
            case_id=case_id,
            code_content=code,
            code_language="python",
            generation_prompt=prompt,
            ai_model=settings.OPENAI_MODEL,
            is_valid=is_valid,
            syntax_error=syntax_error,
        )
        self._db.add(gen_code)

        case.status = "generated"
        self._db.commit()
        self._db.refresh(gen_code)

        return GenerateResult(
            code_id=gen_code.id,
            code_content=code,
            is_valid=bool(is_valid),
            syntax_error=syntax_error,
            ai_model=settings.OPENAI_MODEL,
        )

    # ═══════════════════════════════════════════════
    # 元素匹配
    # ═══════════════════════════════════════════════

    @staticmethod
    def _match_elements(
        steps: list[dict],
        elements: list[PageElement]
    ) -> list[dict]:
        """对每个步骤的 target 进行智能匹配

        - 如果 target 是 CSS/XPath 选择器 → 直接使用
        - 否则在元素列表中模糊匹配 text_content / placeholder / name
        - 阈值 0.6，将最佳匹配的 selector 注入 step
        """
        matched = []
        for step in steps:
            target = step.get("target", "")
            step_copy = dict(step)

            # 已是选择器，跳过匹配
            if SELECTOR_PATTERN.search(target):
                matched.append(step_copy)
                continue

            if not target:
                matched.append(step_copy)
                continue

            # 模糊匹配
            best_score = 0.0
            best_element = None

            for el in elements:
                # 对比三个字段
                candidates = []
                if el.text_content:
                    candidates.append(el.text_content[:200])
                if el.placeholder:
                    candidates.append(el.placeholder)
                if el.name:
                    candidates.append(el.name)

                for candidate in candidates:
                    if not candidate:
                        continue
                    score = difflib.SequenceMatcher(
                        None, target, candidate
                    ).ratio()
                    if score > best_score:
                        best_score = score
                        best_element = el

            if best_element and best_score >= 0.6:
                step_copy["target"] = best_element.selector
                step_copy["description"] = step_copy.get("description", "") or f"匹配元素: {best_element.text_content or best_element.selector}"
                step_copy["_matched_selector"] = best_element.selector
                step_copy["_match_score"] = round(best_score, 2)
            else:
                # 匹配失败，标注
                step_copy["_unmatched"] = True

            matched.append(step_copy)

        return matched

    # ═══════════════════════════════════════════════
    # 批量生成
    # ═══════════════════════════════════════════════

    def generate_batch_sync(
        self, project_id: int, case_ids: list[int], batch_job: BatchJob
    ) -> None:
        """批量生成（同步方法，由后台任务线程调用）"""
        for cid in case_ids:
            try:
                self.generate_single(project_id, cid)
                batch_job.completed += 1
            except Exception:
                batch_job.failed += 1
        batch_job.status = "completed"

    def generate_batch(
        self, project_id: int, case_ids: list[int]
    ) -> list[dict]:
        """批量生成（编排器调用，异常隔离：单个失败不影响其他）"""
        results = []
        for cid in case_ids:
            try:
                result = self.generate_single(project_id, cid)
                results.append({"case_id": cid, "status": "success", "code_id": result.code_id})
            except Exception as e:
                results.append({"case_id": cid, "status": "failed", "error": str(e)[:200]})
        return results

    # ═══════════════════════════════════════════════
    # 查询最新代码
    # ═══════════════════════════════════════════════

    def get_latest_code(self, project_id: int, case_id: int) -> dict:
        """获取用例最新生成的代码"""
        # 校验用例存在
        case = (
            self._db.query(TestCase)
            .filter(TestCase.id == case_id, TestCase.project_id == project_id)
            .first()
        )
        if not case:
            raise AIException(f"用例 {case_id} 不存在")

        gen_code = (
            self._db.query(GeneratedCode)
            .filter(GeneratedCode.case_id == case_id)
            .order_by(GeneratedCode.created_at.desc())
            .first()
        )
        if not gen_code:
            raise AIException(f"用例 {case_id} 尚无生成的代码")

        return {
            "code_id": gen_code.id,
            "code_content": gen_code.code_content,
            "is_valid": bool(gen_code.is_valid),
            "syntax_error": gen_code.syntax_error,
            "is_healed": bool(gen_code.is_healed),
            "ai_model": gen_code.ai_model,
            "created_at": str(gen_code.created_at) if gen_code.created_at else "",
        }


# ═══════════════════════════════════════════════
# 模块级辅助函数
# ═══════════════════════════════════════════════

def _build_prompt(
    case_name: str,
    pre_condition: str,
    expected_result: str,
    steps_json: str,
    elements_list: str,
) -> str:
    """从文件加载 Prompt 模板并填充变量（每次读取，支持热更新）"""
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "prompts", "generate_prompt.txt"
    )
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        # 兜底模板
        template = (
            "生成 Playwright Python 异步测试代码。\n"
            "用例: {case_name}\n步骤: {steps_json}\n元素: {elements_list}"
        )

    return template.format(
        case_name=case_name,
        pre_condition=pre_condition,
        expected_result=expected_result,
        steps_json=steps_json,
        elements_list=elements_list,
    )


def _format_elements(elements: list[PageElement]) -> str:
    """格式化元素列表为可读文本"""
    if not elements:
        return "（无页面元素数据）"

    lines = []
    for el in elements:
        info = f"- [{el.element_type}] tag={el.tag_name}"
        if el.element_id:
            info += f" id={el.element_id}"
        if el.name:
            info += f" name={el.name}"
        if el.class_name:
            info += f" class={el.class_name}"
        if el.text_content:
            text = el.text_content[:80].replace("\n", " ")
            info += f' text="{text}"'
        if el.placeholder:
            info += f' placeholder="{el.placeholder}"'
        info += f" selector={el.selector}"
        lines.append(info)

    return "\n".join(lines)


def _call_openai(prompt: str, model: str, retries: int = 3) -> str:
    """调用 OpenAI API，带指数退避重试

    Args:
        prompt: 用户消息
        model: 模型名
        retries: 最大重试次数

    Returns:
        LLM 返回的原始文本
    """
    if not settings.OPENAI_API_KEY:
        return _mock_code()

    last_error = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{settings.OPENAI_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是一名精通 Playwright Python 异步 API 的自动化测试专家。只输出 Python 代码，不含解释。",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 4096,
                    },
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                logger.info(
                    "AI 调用成功, model=%s, tokens=%s",
                    model,
                    body.get("usage", {}).get("total_tokens", "?"),
                )
                return content
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning("AI 调用失败(第%d次), %ds后重试: %s", attempt + 1, wait, e)
                time.sleep(wait)

    raise AIException(f"AI 服务调用失败(已重试{retries}次): {last_error}")


def _extract_code(raw: str) -> str:
    """从 LLM 输出中提取纯 Python 代码

    去除 markdown 代码块标记（```python ... ```）及前后空白。
    """
    code = raw.strip()

    # 匹配 ```python ... ``` 或 ``` ... ```
    for lang in ("python", ""):
        prefix = f"```{lang}"
        start = code.find(prefix)
        if start != -1:
            inner = code[start + len(prefix):]
            end = inner.rfind("```")
            if end != -1:
                return inner[:end].strip()
            return inner.strip()

    return code


def _validate_syntax(code: str) -> None:
    """使用 ast.parse 校验 Python 语法"""
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise SyntaxError(f"语法错误 (行 {e.lineno}, 列 {e.offset}): {e.msg}")


def _security_check(code: str) -> None:
    """安全黑名单检查：禁止危险导入和内置函数

    Raises:
        SecurityException: 检测到危险代码
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # 语法错误已在 _validate_syntax 处拦截

    # 检查 import
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in BANNED_IMPORTS:
                    raise SecurityException(
                        f"禁止导入模块: {name}（行 {node.lineno}）"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split(".")[0]
                if name in BANNED_IMPORTS:
                    raise SecurityException(
                        f"禁止导入模块: {name}（行 {node.lineno}）"
                    )

    # 检查内置函数调用
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_BUILTINS:
                raise SecurityException(
                    f"禁止使用函数: {func.id}()（行 {node.lineno}）"
                )


def _mock_code() -> str:
    """无 API Key 时返回 Mock 代码"""
    return '''from playwright.async_api import Page, expect
import asyncio
from datetime import datetime


async def run_test(page: Page) -> dict:
    """Mock — 请配置 OPENAI_API_KEY"""
    steps_result = []
    start_time = datetime.now()
    try:
        print("[执行] Mock 测试 - 请配置 OPENAI_API_KEY")
        await page.goto("https://example.com")
        await page.wait_for_load_state("networkidle")

        screenshot_path = "reports/screenshots/mock_test.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        print(f"[截图] 已保存: {screenshot_path}")

        steps_result.append({"step": 1, "status": "passed", "action": "navigate"})
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "steps": steps_result,
        }

    duration = (datetime.now() - start_time).total_seconds()
    return {
        "success": True,
        "message": f"测试通过, {len(steps_result)} 步, {duration:.1f}s",
        "steps": steps_result,
    }
'''
