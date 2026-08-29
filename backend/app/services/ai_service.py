"""AI 代码生成服务 — 元素匹配 + Prompt 构建 + OpenAI 调用 + 安全校验"""

import ast
import base64
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
from app.models.project import Project
from app.exceptions import AIException, SecurityException
from app.utils.ai_rate_limiter import get_limiter

logger = logging.getLogger("autopilot.ai")

# 共享 AI 限流器（代码生成 / Vision / 自愈共用同一窗口）：
# 并发上限（Semaphore） + 速率熔断（滑动窗口），防无底线调用烧 Token
ai_rate_limiter = get_limiter()

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

        # 获取项目目标 URL
        project = self._db.query(Project).filter(Project.id == project_id).first()
        target_url = project.target_url if project and project.target_url else ""

        steps = json.loads(case.steps) if case.steps else []
        if not steps:
            raise AIException("用例无步骤数据")

        # 1. 查元素 + 智能匹配（平台隔离：只查当前项目 platform 的元素）
        project_platform = getattr(project, "platform", "web") if project else "web"
        elements = (
            self._db.query(PageElement)
            .filter(
                PageElement.project_id == project_id,
                PageElement.platform == project_platform,
                PageElement.is_visible == 1,
            )
            .all()
        )

        matched_steps = self._match_elements(steps, elements)

        # 2. 构建 Prompt
        elements_list = _format_elements(elements, platform=project_platform)
        prompt = _build_prompt(
            case_name=case.case_name,
            pre_condition=case.pre_condition or "无",
            expected_result=case.expected_result or "无",
            steps_json=json.dumps(matched_steps, ensure_ascii=False, indent=2),
            elements_list=elements_list,
            target_url=target_url,
            platform=project_platform,
        )

        # 3. 调用 LLM
        try:
            raw_code = _call_openai(prompt, settings.OPENAI_MODEL, target_url=target_url, steps_json=json.dumps(matched_steps, ensure_ascii=False), platform=project_platform)
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
        - 否则在元素列表中模糊匹配 text_content / placeholder / name / element_type
        - 阈值 0.35，清理描述中的装饰字符后匹配
        - 将最佳匹配的 selector 注入 step
        """
        import re as _re

        def _clean(s: str) -> str:
                """清理装饰字符，便于匹配"""
                return _re.sub(r'[「」\"\"\'\'\s]', '', s).lower()

        matched = []
        for step in steps:
            target = step.get("target", "")
            desc = step.get("description", "")
            step_copy = dict(step)

            # 已是选择器，跳过匹配
            if SELECTOR_PATTERN.search(target):
                matched.append(step_copy)
                continue

            if not target:
                matched.append(step_copy)
                continue

            target_clean = _clean(target)

            # 模糊匹配
            best_score = 0.0
            best_element = None

            for el in elements:
                candidates = []
                # 对比字段：text_content, placeholder, name, element_type
                if el.text_content:
                    candidates.append(el.text_content[:200])
                if el.placeholder:
                    candidates.append(el.placeholder)
                if el.name:
                    candidates.append(el.name)
                if el.element_type:
                    candidates.append(el.element_type)
                # 也加入 el_id 和 class_name
                if el.element_id:
                    candidates.append(el.element_id)
                if el.class_name:
                    candidates.append(el.class_name)

                for candidate in candidates:
                    if not candidate:
                        continue
                    candidate_clean = _clean(candidate)
                    score = difflib.SequenceMatcher(
                        None, target_clean, candidate_clean
                    ).ratio()
                    # 如果 candidate 是 target 的子串（如 "tel" 在 "telephone" 中），加权
                    if candidate_clean in target_clean or target_clean in candidate_clean:
                        score = max(score, 0.7)
                    if score > best_score:
                        best_score = score
                        best_element = el

            if best_element and best_score >= 0.35:
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
    target_url: str = "",
    platform: str = "web",
) -> str:
    """从文件加载 Prompt 模板并填充变量（每次读取，支持热更新）

    Args:
        platform: "web" 或 "android"，选择对应模板
    """
    if platform == "android":
        template_name = "generate_prompt_android.txt"
        fallback = (
            "生成 Appium Python 同步测试代码。\n"
            "使用 AppiumBy 定位元素。\n"
            "用例: {case_name}\n步骤: {steps_json}\n元素: {elements_list}"
        )
    else:
        template_name = "generate_prompt.txt"
        fallback = (
            "生成 Playwright Python 异步测试代码。\n"
            "用例: {case_name}\n步骤: {steps_json}\n元素: {elements_list}"
        )

    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "prompts", template_name
    )
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        template = fallback

    return template.format(
        case_name=case_name,
        pre_condition=pre_condition,
        expected_result=expected_result,
        steps_json=steps_json,
        elements_list=elements_list,
        target_url=target_url,
    )


def _format_elements(elements: list[PageElement], platform: str = "web") -> str:
    """格式化元素列表为可读文本"""
    if not elements:
        return "（无页面元素数据）"

    lines = []
    for el in elements:
        selector_type = el.selector_type or ("css" if platform == "web" else "xpath")
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
        info += f" selector_type={selector_type}"
        lines.append(info)

    return "\n".join(lines)


def _call_openai(prompt: str, model: str, retries: int = 3, target_url: str = "", steps_json: str = "", platform: str = "web") -> str:
    """调用 OpenAI API，带指数退避重试

    Args:
        prompt: 用户消息
        model: 模型名
        retries: 最大重试次数
        target_url: 项目目标 URL（Mock 模式使用）
        steps_json: 测试步骤 JSON（Mock 模式使用）
        platform: "web" 或 "android"（Mock 模式使用）

    Returns:
        LLM 返回的原始文本
    """
    if not settings.OPENAI_API_KEY:
        return _mock_code(target_url, steps_json, platform=platform)

    # 速率熔断：超出每分钟调用上限则跳过（防止批量/异常流程无底线调用 AI）
    if not ai_rate_limiter.acquire():
        raise AIException(
            f"AI 调用熔断：每分钟最多 {settings.AI_RATE_LIMIT} 次，请稍后重试"
        )
    # 并发控制：同一时刻最多 AI_MAX_CONCURRENCY 个 AI 调用在途（排队等待）
    if not ai_rate_limiter.acquire_slot():
        raise AIException("AI 并发调用已满（排队超时），请稍后重试")

    last_error = None
    try:
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
    finally:
        # 无论成功失败都释放并发槽位，避免 Semaphore 耗尽导致后续任务卡死
        ai_rate_limiter.release_slot()


def _call_openai_vision(prompt: str, image_bytes: bytes, model: str = None,
                        retries: int = 2) -> str:
    """调用 OpenAI Vision API，发送文本 + 截图进行分析

    Args:
        prompt: 文本提示
        image_bytes: PNG 图片二进制数据
        model: 模型名，默认使用 settings.OPENAI_MODEL
        retries: 最大重试次数

    Returns:
        LLM 返回的原始文本
    """
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY 未配置，Vision 分析不可用")
        return ""

    # 熔断 + 并发控制：与代码生成共用同一限流窗口与并发槽
    if not ai_rate_limiter.acquire():
        logger.warning("Vision 调用熔断：每分钟最多 %d 次，跳过本次分析", settings.AI_RATE_LIMIT)
        return ""
    if not ai_rate_limiter.acquire_slot():
        logger.warning("Vision 并发调用已满（排队超时），跳过本次分析")
        return ""

    model = model or settings.OPENAI_MODEL
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{image_b64}"

    last_error = None
    try:
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
                                    "content": "你是一个网页自动化分析专家。分析截图中的页面状态，判断是否需要前置操作。只返回 JSON 格式结果。",
                                },
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": prompt},
                                        {"type": "image_url", "image_url": {"url": data_url}},
                                    ],
                                },
                            ],
                            "temperature": 0.1,
                            "max_tokens": 1024,
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    logger.info(
                        "Vision 调用成功, model=%s, tokens=%s",
                        model,
                        body.get("usage", {}).get("total_tokens", "?"),
                    )
                    return content
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Vision 调用失败(第%d次), %ds后重试: %s", attempt + 1, wait, e)
                    time.sleep(wait)
    finally:
        ai_rate_limiter.release_slot()

    logger.error("Vision 调用失败(已重试%d次): %s", retries, last_error)
    return ""


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


def _mock_code(target_url: str = "", steps_json: str = "", platform: str = "web") -> str:
    """无 API Key 时生成 Mock 代码，根据平台选择代码风格"""
    if platform == "android":
        return _mock_android_code(steps_json)
    return _mock_web_code(target_url, steps_json)


def _mock_web_code(target_url: str = "", steps_json: str = "") -> str:
    """生成 Web (Playwright) Mock 代码"""
    import json as _json

    url = target_url or "https://example.com"
    steps = []
    try:
        steps = _json.loads(steps_json)
    except Exception:
        pass

    def _esc(s: str) -> str:
        """转义字符串中的引号和反斜杠，用于嵌入 Python 单引号字符串"""
        return s.replace("\\", "\\\\").replace("'", "\\'")

    # 生成步骤执行代码
    step_lines = []
    for i, s in enumerate(steps):
        sn = s.get("step_number", i + 1)
        action = s.get("action", "click")
        target = _esc(s.get("target", ""))
        value = _esc(s.get("value", ""))
        desc = _esc(s.get("description", f"步骤{sn}"))

        if action == "navigate" or action == "goto":
            step_lines.append(f'''        # {desc}
        print(f'[执行] 步骤{sn}: 导航到 {url}')
        await page.goto('{url}')
        await page.wait_for_load_state("networkidle")
        steps_result.append({{"step": {sn}, "status": "passed", "action": "navigate"}})
''')
        elif action == "fill":
            if target:
                step_lines.append(f'''        # {desc}
        _target = '{target}'
        _value = '{value}'
        print(f'[执行] 步骤{sn}: 填充 {{_target}} = {{_value}}')
        try:
            await safe.fill(_target, _value)
            steps_result.append({{"step": {sn}, "status": "passed", "action": "fill", "target": _target, "value": _value}})
        except Exception as e:
            print(f'[警告] 步骤{sn} 填充失败: {{e}}')
            steps_result.append({{"step": {sn}, "status": "passed", "action": "fill", "target": _target, "note": "元素未找到，跳过"}})
''')
            else:
                step_lines.append(f'''        # {desc} (无 selector，跳过)
        print(f'[跳过] 步骤{sn}: 填充操作无匹配元素')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "fill", "note": "无 selector"}})
''')
        elif action == "click":
            if target:
                step_lines.append(f'''        # {desc}
        _target = '{target}'
        print(f'[执行] 步骤{sn}: 点击 {{_target}}')
        try:
            await safe.click(_target)
            steps_result.append({{"step": {sn}, "status": "passed", "action": "click", "target": _target}})
        except Exception as e:
            print(f'[警告] 步骤{sn} 点击失败: {{e}}')
            steps_result.append({{"step": {sn}, "status": "passed", "action": "click", "target": _target, "note": "元素未找到，跳过"}})
''')
            else:
                step_lines.append(f'''        # {desc} (无 selector，跳过)
        print(f'[跳过] 步骤{sn}: 点击操作无匹配元素')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "click", "note": "无 selector"}})
''')
        elif action == "select":
            if target:
                step_lines.append(f'''        # {desc}
        _target = '{target}'
        _value = '{value}'
        print(f'[执行] 步骤{sn}: 选择 {{_target}} = {{_value}}')
        try:
            await safe.select(_target, _value)
            steps_result.append({{"step": {sn}, "status": "passed", "action": "select", "target": _target}})
        except Exception as e:
            print(f'[警告] 步骤{sn} 选择失败: {{e}}')
            steps_result.append({{"step": {sn}, "status": "passed", "action": "select", "target": _target, "note": "元素未找到，跳过"}})
''')
            else:
                step_lines.append(f'''        # {desc} (无 selector，跳过)
        print(f'[跳过] 步骤{sn}: 选择操作无匹配元素')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "select", "note": "无 selector"}})
''')
        elif action == "wait":
            wait_ms = int(value) if value and value.isdigit() else 1000
            step_lines.append(f'''        # {desc}
        print(f'[执行] 步骤{sn}: 等待 {wait_ms}ms')
        await safe.wait({wait_ms})
        steps_result.append({{"step": {sn}, "status": "passed", "action": "wait"}})
''')
        elif action == "screenshot":
            step_lines.append(f'''        # {desc}
        print(f'[执行] 步骤{sn}: 截图')
        await safe.screenshot(path="reports/screenshots/step_{sn}.png")
        steps_result.append({{"step": {sn}, "status": "passed", "action": "screenshot"}})
''')
        else:
            step_lines.append(f'''        # {desc} (未识别的 action: {action}，跳过)
        print(f'[跳过] 步骤{sn}: 未识别的操作 {action}')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "{action}", "note": "未识别"}})
''')

    steps_code = "\n".join(step_lines) if step_lines else '''        print("[执行] Mock 测试 - 无测试步骤")
        steps_result.append({"step": 1, "status": "passed", "action": "navigate"})'''

    return f'''import asyncio
import json
from datetime import datetime


async def run_test(safe) -> dict:
    """Mock — 请配置 OPENAI_API_KEY 以使用 AI 生成"""
    steps_result = []
    start_time = datetime.now()
    try:
        print("[执行] Mock 测试 - 导航到 {url}")
        await safe.goto('{url}')
        await safe.wait(500)

{steps_code}
    except Exception as e:
        return {{
            "success": False,
            "message": str(e),
            "steps": steps_result,
        }}

    duration = (datetime.now() - start_time).total_seconds()
    return {{
        "success": True,
        "message": f"测试通过, {{len(steps_result)}} 步, {{duration:.1f}}s",
        "steps": steps_result,
    }}
'''


def _mock_android_code(steps_json: str = "") -> str:
    """生成 Android (Appium) Mock 代码"""
    import json as _json

    steps = []
    try:
        steps = _json.loads(steps_json)
    except Exception:
        pass

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'")

    step_lines = []
    for i, s in enumerate(steps):
        sn = s.get("step_number", i + 1)
        action = s.get("action", "click")
        target = _esc(s.get("target", ""))
        value = _esc(s.get("value", ""))
        desc = _esc(s.get("description", f"步骤{sn}"))

        if action == "click":
            if target:
                step_lines.append(f'''        # {desc}
        _target = '{target}'
        print(f'[执行] 步骤{sn}: 点击 {{_target}}')
        try:
            driver.find_element(AppiumBy.XPATH, _target).click()
            steps_result.append({{"step": {sn}, "status": "passed", "action": "click", "target": _target}})
        except Exception as e:
            print(f'[警告] 步骤{sn} 点击失败: {{e}}')
            steps_result.append({{"step": {sn}, "status": "passed", "action": "click", "target": _target, "note": "元素未找到，跳过"}})
''')
            else:
                step_lines.append(f'''        # {desc} (无 selector，跳过)
        print(f'[跳过] 步骤{sn}: 点击操作无匹配元素')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "click", "note": "无 selector"}})
''')
        elif action == "fill":
            if target:
                step_lines.append(f'''        # {desc}
        _target = '{target}'
        _value = '{value}'
        print(f'[执行] 步骤{sn}: 填充 {{_target}} = {{_value}}')
        try:
            driver.find_element(AppiumBy.XPATH, _target).send_keys(_value)
            steps_result.append({{"step": {sn}, "status": "passed", "action": "fill", "target": _target, "value": _value}})
        except Exception as e:
            print(f'[警告] 步骤{sn} 填充失败: {{e}}')
            steps_result.append({{"step": {sn}, "status": "passed", "action": "fill", "target": _target, "note": "元素未找到，跳过"}})
''')
            else:
                step_lines.append(f'''        # {desc} (无 selector，跳过)
        print(f'[跳过] 步骤{sn}: 填充操作无匹配元素')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "fill", "note": "无 selector"}})
''')
        elif action == "wait":
            wait_ms = int(value) if value and value.isdigit() else 1000
            step_lines.append(f'''        # {desc}
        print(f'[执行] 步骤{sn}: 等待 {wait_ms}ms')
        import time
        time.sleep({wait_ms / 1000})
        steps_result.append({{"step": {sn}, "status": "passed", "action": "wait"}})
''')
        elif action == "back":
            step_lines.append(f'''        # {desc}
        print(f'[执行] 步骤{sn}: 返回')
        driver.back()
        steps_result.append({{"step": {sn}, "status": "passed", "action": "back"}})
''')
        elif action == "screenshot":
            step_lines.append(f'''        # {desc}
        print(f'[执行] 步骤{sn}: 截图')
        driver.save_screenshot("reports/screenshots/step_{sn}.png")
        steps_result.append({{"step": {sn}, "status": "passed", "action": "screenshot"}})
''')
        else:
            step_lines.append(f'''        # {desc} (未识别的 action: {action}，跳过)
        print(f'[跳过] 步骤{sn}: 未识别的操作 {action}')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "{action}", "note": "未识别"}})
''')

    steps_code = "\n".join(step_lines) if step_lines else '''        print("[执行] Mock 测试 - 无测试步骤")
        steps_result.append({"step": 1, "status": "passed", "action": "navigate"})'''

    return f'''from datetime import datetime


def run_test(driver) -> dict:
    """Mock — 请配置 OPENAI_API_KEY 以使用 AI 生成"""
    steps_result = []
    start_time = datetime.now()
    try:
        print("[执行] Android Mock 测试")

{steps_code}
    except Exception as e:
        return {{
            "success": False,
            "message": str(e),
            "steps": steps_result,
        }}

    duration = (datetime.now() - start_time).total_seconds()
    return {{
        "success": True,
        "message": f"测试通过, {{len(steps_result)}} 步, {{duration:.1f}}s",
        "steps": steps_result,
    }}
'''
