"""AI 代码生成服务 — 元素匹配 + Prompt 构建 + OpenAI 调用 + 安全校验"""

import ast
import asyncio
import base64
import difflib
import json
import logging
import os
import random
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
from app.exceptions import AIException, DeadlineExceeded
from app.utils.ai_rate_limiter import get_limiter
from app.utils.code_validator import CodeValidator
from app.utils.step_canonicalizer import hash_steps
from app.utils.url_builder import build_target_url

logger = logging.getLogger("autopilot.ai")

# 共享 AI 限流器（代码生成 / Vision / 自愈共用同一窗口）：
# 并发上限（Semaphore） + 速率熔断（滑动窗口），防无底线调用烧 Token
ai_rate_limiter = get_limiter()

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

    def generate_single(self, project_id: int, case_id: int,
                        *, remaining: Optional[float] = None) -> GenerateResult:
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
        # P1-1：AI Prompt 构建属 Admission 前上下文，用 Project 当前值（target_url + test_path）
        project = self._db.query(Project).filter(Project.id == project_id).first()
        target_url = build_target_url(project.target_url, project.test_path) if project else ""

        steps = json.loads(case.steps) if case.steps else []
        if not steps:
            raise AIException("用例无步骤数据")

        # 步骤来源哈希：记录本次生成基于的 TestCase.steps，供执行期来源绑定
        source_steps_hash = hash_steps(steps)

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
        # Android：从项目 config_json 取 app_package/app_activity，注入 prompt（供 navigate 原语）
        app_package, app_activity = "", ""
        if project_platform == "android" and project and project.config_json:
            try:
                cfg = json.loads(project.config_json) if isinstance(project.config_json, str) else project.config_json
                app_package = str(cfg.get("app_package") or "")
                app_activity = str(cfg.get("app_activity") or "")
            except (ValueError, TypeError):
                pass
        prompt = _build_prompt(
            case_name=case.case_name,
            pre_condition=case.pre_condition or "无",
            expected_result=case.expected_result or "无",
            steps_json=json.dumps(matched_steps, ensure_ascii=False, indent=2),
            elements_list=elements_list,
            target_url=target_url,
            platform=project_platform,
            app_package=app_package,
            app_activity=app_activity,
        )

        # 3. 调用 LLM
        try:
            raw_code = _call_openai(prompt, settings.OPENAI_MODEL, target_url=target_url, steps_json=json.dumps(matched_steps, ensure_ascii=False), platform=project_platform, remaining=remaining)
        except AIException:
            # 透传原始 AIException，保留 error_type（deadline/quota/slot/read_timeout 等）
            # 与 retryable，供 Batch 层 KPI exclusion bucket 精确分类；禁止统一包裹吞掉信号。
            raise
        except Exception as e:
            raise AIException(f"AI 服务调用失败: {str(e)}")

        # 4. 提取代码
        code = _extract_code(raw_code)

        # 5. 校验（CodeValidator 为唯一代码校验来源，闭合并生成/执行两套规则缝隙）
        syntax_error = None
        is_valid = 1
        error = CodeValidator.validate(code, platform=project_platform)
        if error:
            syntax_error = error
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
            source_steps_hash=source_steps_hash,
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
    # AI TestCase 候选生成（EXT-AITC-10A）
    # ═══════════════════════════════════════════════

    def generate_test_cases(self, prompt: str,
                            *, remaining: Optional[float] = None) -> str:
        """生成 TestCase 候选（JSON 文本）

        强制复用既有调用链（_call_openai → _chat_http_attempt），天然满足
        §13（1 Attempt = 1 Quota = 1 Slot）与超时/限流治理；禁止新写 HTTP 客户端。
        无 API Key（Mock 模式）→ 返回确定性 mock JSON，不发起真实调用。
        """
        if not settings.OPENAI_API_KEY:
            return _mock_case_json()
        return _call_openai(prompt, settings.OPENAI_MODEL, remaining=remaining)

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
    app_package: str = "",
    app_activity: str = "",
) -> str:
    """从文件加载 Prompt 模板并填充变量（每次读取，支持热更新）

    Args:
        platform: "web" 或 "android"，选择对应模板
        app_package / app_activity: Android 项目 config_json 中的应用包名/启动 Activity，
            仅在 android 平台由调用方传入（供模板里的 navigate 原语与日期断言引导使用）
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
        app_package=app_package,
        app_activity=app_activity,
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


def _make_deadline(remaining: Optional[float]) -> float:
    """把 remaining 预算换算成绝对 monotonic 截止时刻（未传时用配置默认）"""
    if remaining is not None:
        return time.monotonic() + remaining
    return time.monotonic() + settings.AI_DEADLINE_DEFAULT_SECONDS


def _remaining(deadline: float) -> Optional[float]:
    """距 deadline 的剩余秒数；deadline 开关关闭时返回 None（视为无上限）"""
    return max(0.0, deadline - time.monotonic())


def _request_timeout(deadline: float) -> httpx.Timeout:
    """四项分离超时；每个分项 = min(配置上限, remaining)，禁止为保下限突破 remaining"""
    rem = _remaining(deadline)
    return httpx.Timeout(
        connect=min(settings.AI_REQUEST_TIMEOUT_CONNECT, rem),
        write=min(settings.AI_REQUEST_TIMEOUT_WRITE, rem),
        read=min(settings.AI_REQUEST_TIMEOUT_READ, rem),
        pool=min(settings.AI_REQUEST_TIMEOUT_POOL, rem),
    )


# 可重试的错误类型（真正发生了一次网络/服务端错误）
# 语义由 _classify_status 与各 httpx 分支决定；此集合用于外部判定/文档
_RETRYABLE_ERROR_TYPES = frozenset({
    "connect_timeout", "read_timeout", "write_timeout", "pool_timeout",
    "timeout", "connect_error", "protocol_error", "rate_limited",
    "server_error", "request_timeout",
})


def _classify_status(status: int) -> tuple[str, bool]:
    """按 HTTP 状态码分类（retryable / non-retryable）"""
    if status in (408, 409, 425, 429):
        return ("rate_limited" if status == 429 else "request_timeout", True)
    if 500 <= status < 600:
        return ("server_error", True)
    return ("http_4xx", False)  # 其他 4xx：non-retryable


@dataclass
class _HTTPAttempt:
    content: Optional[str] = None
    error_type: Optional[str] = None
    retryable: bool = False
    http_status: Optional[int] = None
    usage: Optional[int] = None
    retry_after: Optional[str] = None


def _log_attempt(*, batch_id, case_id, attempt, error_type, http_status,
                 latency_ms, usage, retry_after):
    """每次 attempt 的结构化日志（成功/失败都记）"""
    logger.info(
        "AI attempt batch=%s case=%s attempt=%s error_type=%s http_status=%s "
        "latency_ms=%s usage=%s retry_after=%s",
        batch_id or "-", case_id or "-", attempt,
        error_type or "-", http_status if http_status is not None else "-",
        f"{latency_ms:.1f}" if latency_ms is not None else "-",
        usage if usage is not None else "-",
        retry_after if retry_after is not None else "-",
    )


async def _chat_http_attempt(
    *, model: str, messages: list, max_tokens: int, attempt: int,
    deadline: float, batch_id=None, case_id=None, vision: bool = False,
) -> _HTTPAttempt:
    """执行一次 HTTP attempt（wall-clock deadline 由 asyncio.wait + 主动 aclose 真正终止）。

    - 统一 wall-clock deadline：分项 timeout 不保证总时长，任一到点即触发硬看门狗。
    - Windows ProactorEventLoop 下 asyncio.wait_for 的 task.cancel() 无法中止已发出的
      overlapped socket recv（协程会卡在 await recv_future 上永久挂起），因此这里改用
      asyncio.wait(FIRST_COMPLETED) + 到点主动 client.aclose() 关闭底层连接，真正终止 I/O，
      且从不 await 被取消的 post_task，杜绝僵尸任务。不同事件循环策略均安全。
    - 返回 _HTTPAttempt；成功时 error_type=None。
    """
    start = time.monotonic()
    http_status = None
    retry_after = None
    usage = None
    error_type = None
    retryable = False
    content = None

    # 早期短路：remaining<=0 不发请求（保证既有 posted==0 语义）
    if _remaining(deadline) <= 0:
        raise DeadlineExceeded()

    client = httpx.AsyncClient(timeout=_request_timeout(deadline))
    headers = {"Content-Type": "application/json"}
    if settings.OPENAI_API_KEY:
        # 空 key 时禁止发送非法头 "Bearer "（尾随空值），否则 httpx 抛 LocalProtocolError
        headers["Authorization"] = f"Bearer {settings.OPENAI_API_KEY}"

    remaining = _remaining(deadline)
    # wall-clock 看门狗：用 loop.call_later 生成定时 future（不经 asyncio.sleep，
    # 一是避免占被 mock 的 sleep 桩，二是与事件循环 timer 可靠对齐，Proactor 下必然准时）
    _loop = asyncio.get_event_loop()
    deadline_fut = _loop.create_future()
    _deadline_handle = _loop.call_later(remaining, deadline_fut.set_result, True)
    post_task = asyncio.ensure_future(
        client.post(
            f"{settings.OPENAI_BASE_URL}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": max_tokens,
            },
        )
    )

    try:
        try:
            done, _ = await asyncio.wait(
                {post_task, deadline_fut},
                timeout=remaining + 1.0,  # 双保险，仅事件循环被占死时兜底
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            _deadline_handle.cancel()
            if not deadline_fut.done():
                deadline_fut.cancel()
        if post_task not in done:
            # 硬看门狗路径：deadline 先到 → 真正中止底层 socket I/O（Proactor 下唯一真终止）
            post_task.cancel()
            try:
                await asyncio.wait_for(client.aclose(), timeout=2.0)
            except Exception:
                pass
            error_type = "deadline_exceeded"
            retryable = False
            _log_attempt(batch_id=batch_id, case_id=case_id, attempt=attempt,
                         error_type=error_type, http_status=http_status,
                         latency_ms=(time.monotonic() - start) * 1000,
                         usage=usage, retry_after=retry_after)
            raise DeadlineExceeded()
        response = post_task.result()
    except DeadlineExceeded:
        raise
    except httpx.HTTPStatusError as e:
        http_status = e.response.status_code
        retry_after = e.response.headers.get("Retry-After")
        error_type, retryable = _classify_status(http_status)
    except httpx.ConnectTimeout:
        error_type, retryable = "connect_timeout", True
    except httpx.ReadTimeout:
        error_type, retryable = "read_timeout", True
    except httpx.WriteTimeout:
        error_type, retryable = "write_timeout", True
    except httpx.PoolTimeout:
        error_type, retryable = "pool_timeout", True
    except httpx.ConnectError:
        error_type, retryable = "connect_error", True
    except httpx.TimeoutException:
        error_type, retryable = "timeout", True
    except httpx.HTTPError:
        error_type, retryable = "protocol_error", True
    else:
        http_status = response.status_code
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            retry_after = e.response.headers.get("Retry-After")
            error_type, retryable = _classify_status(e.response.status_code)
        else:
            # 200：解析响应，响应返回后再校 elapsed，超 deadline 一律 deadline_exceeded
            if _remaining(deadline) <= 0:
                raise DeadlineExceeded()
            try:
                body = response.json()
            except (ValueError, Exception):  # invalid JSON
                error_type, retryable = "invalid_json", False
            else:
                if not isinstance(body, dict):
                    error_type, retryable = "schema_error", False
                else:
                    usage = body.get("usage", {}).get("total_tokens")
                    try:
                        choices = body["choices"]
                        content = choices[0]["message"]["content"]
                    except (KeyError, IndexError, TypeError):
                        error_type, retryable = "schema_error", False
    finally:
        # 无论成功/失败/超时，都关闭 client；DeadlineExceeded 分支已主动 aclose，
        # 此处二度调用幂等且安全
        try:
            await client.aclose()
        except Exception:
            pass

    latency_ms = (time.monotonic() - start) * 1000
    _log_attempt(batch_id=batch_id, case_id=case_id, attempt=attempt,
                 error_type=error_type, http_status=http_status,
                 latency_ms=latency_ms, usage=usage, retry_after=retry_after)

    return _HTTPAttempt(content=content, error_type=error_type,
                        retryable=retryable, http_status=http_status,
                        usage=usage, retry_after=retry_after)


def _retry_wait(attempt: int, retry_after: Optional[str], deadline: float) -> float:
    """计算 backoff 时长：尊重 Retry-After，否则 base*2^(attempt-1)+jitter；均受 deadline 钳制"""
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise DeadlineExceeded()
    desired = 0.0
    if retry_after:
        try:
            desired = float(retry_after)
        except (TypeError, ValueError):
            desired = settings.AI_RETRY_BASE * (2 ** (attempt - 1))
    else:
        desired = settings.AI_RETRY_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
    return min(desired, remaining)


async def _call_openai_async(
    prompt: str, model: str, retries: int, deadline: float,
    platform: str, batch_id=None, case_id=None,
) -> str:
    """Chat 调用（async）：deadline 贯穿整个 attempt 链"""
    messages = [
        {"role": "system",
         "content": "你是一名精通 Playwright Python 异步 API 的自动化测试专家。只输出 Python 代码，不含解释。"},
        {"role": "user", "content": prompt},
    ]
    for attempt in range(1, retries + 1):
        remaining = _remaining(deadline)
        if remaining <= 0:
            raise DeadlineExceeded()
        # 原子预留 quota + slot；任一失败本次 attempt 不成立（不计 attempt，不 backoff）
        reservation = await ai_rate_limiter.acquire_attempt_async(remaining)
        if reservation == "quota_timeout":
            raise AIException(
                f"AI 调用熔断：每分钟最多 {settings.AI_RATE_LIMIT} 次，请稍后重试",
                error_type="quota_timeout", retryable=False,
            )
        if reservation == "slot_timeout":
            raise AIException(
                "AI 并发调用已满（排队超时），请稍后重试",
                error_type="slot_timeout", retryable=False,
            )
        try:
            result = await _chat_http_attempt(
                model=model, messages=messages, max_tokens=4096,
                attempt=attempt, deadline=deadline,
                batch_id=batch_id, case_id=case_id,
            )
        finally:
            ai_rate_limiter.release_slot()  # 每次 attempt 结束即释放 slot；backoff 不占 slot

        if result.error_type is None:
            return result.content
        if not result.retryable:
            raise AIException(f"AI 调用失败: {result.error_type}",
                              error_type=result.error_type, retryable=False)
        if attempt >= retries:
            raise AIException(f"AI 服务调用失败(已重试{retries}次): {result.error_type}",
                              error_type=result.error_type, retryable=True)
        await asyncio.sleep(_retry_wait(attempt, result.retry_after, deadline))
    raise AIException(f"AI 服务调用失败(已重试{retries}次)",
                      error_type="unknown", retryable=False)


def _call_openai(prompt: str, model: str, retries: int = settings.AI_RETRY_MAX_ATTEMPTS,
                 target_url: str = "", steps_json: str = "", platform: str = "web",
                 *, remaining: Optional[float] = None,
                 batch_id=None, case_id=None) -> str:
    """调用 OpenAI API，带分类重试与 wall-clock deadline（Mock 模式原样返回）

    Args:
        prompt: 用户消息
        model: 模型名
        retries: 最多 HTTP attempt 次数
        target_url / steps_json / platform: Mock 模式使用
        remaining: 本 case 剩余预算（秒），None 用配置默认上限（跨整个 attempt 链）
        batch_id / case_id: 结构化日志上下文

    Returns:
        LLM 返回的原始文本
    """
    if not settings.OPENAI_API_KEY:
        return _mock_code(target_url, steps_json, platform=platform)

    deadline = _make_deadline(remaining)
    return asyncio.run(
        _call_openai_async(prompt, model, retries, deadline, platform,
                           batch_id=batch_id, case_id=case_id)
    )


# ── Vision 调用（同一 deadline / 重试分类 / 结构化日志机制）──

async def _call_openai_vision_async(
    prompt: str, image_bytes: bytes, model: str, retries: int, deadline: float,
    batch_id=None, case_id=None,
) -> str:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{image_b64}"
    messages = [
        {"role": "system",
         "content": "你是一个网页自动化分析专家。分析截图中的页面状态，判断是否需要前置操作。只返回 JSON 格式结果。"},
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    for attempt in range(1, retries + 1):
        remaining = _remaining(deadline)
        if remaining <= 0:
            return ""
        reservation = await ai_rate_limiter.acquire_attempt_async(remaining)
        if reservation in ("quota_timeout", "slot_timeout"):
            logger.warning("Vision 调用受限(%s)，跳过本次分析", reservation)
            return ""
        try:
            result = await _chat_http_attempt(
                model=model, messages=messages, max_tokens=1024,
                attempt=attempt, deadline=deadline, vision=True,
                batch_id=batch_id, case_id=case_id,
            )
        finally:
            ai_rate_limiter.release_slot()
        if result.error_type is None:
            return result.content
        if not result.retryable:
            return ""
        if attempt >= retries:
            return ""
        await asyncio.sleep(_retry_wait(attempt, result.retry_after, deadline))
    return ""


def _call_openai_vision(prompt: str, image_bytes: bytes, model: str = None,
                        retries: int = 2, *, remaining: Optional[float] = None,
                        batch_id=None, case_id=None) -> str:
    """调用 OpenAI Vision API，发送文本 + 截图进行分析

    Args:
        prompt: 文本提示
        image_bytes: PNG 图片二进制数据
        model: 模型名，默认使用 settings.OPENAI_MODEL
        retries: 最大重试次数
        remaining: 本 case 剩余预算（秒），None 用默认

    Returns:
        LLM 返回的原始文本；失败返回 ""
    """
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY 未配置，Vision 分析不可用")
        return ""
    model = model or settings.OPENAI_MODEL
    deadline = _make_deadline(remaining)
    return asyncio.run(
        _call_openai_vision_async(prompt, image_bytes, model, retries, deadline,
                                  batch_id=batch_id, case_id=case_id)
    )


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


def _mock_case_json() -> str:
    """Mock 模式下的确定性 AI TestCase 候选（零副作用，供无 Key 环境与测试）"""
    return json.dumps({
        "schema_version": 1,
        "cases": [{
            "case_name": "Mock 候选用例",
            "priority": "P1",
            "preconditions": [],
            "steps": [{"step_number": 1, "action": "navigate",
                       "target": "https://example.com"}],
            "expected_result": "页面加载成功",
            "ai_assessment": "needs_review",
        }],
    }, ensure_ascii=False)


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
        await safe.goto('{url}')
        await safe.wait(500)
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
        await safe.screenshot(path="uploads/screenshots/step_{sn}.png")
        steps_result.append({{"step": {sn}, "status": "passed", "action": "screenshot"}})
''')
        else:
            step_lines.append(f'''        # {desc} (未识别的 action: {action}，跳过)
        print(f'[跳过] 步骤{sn}: 未识别的操作 {action}')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "{action}", "note": "未识别"}})
''')

    steps_code = "\n".join(step_lines) if step_lines else '''        print("[执行] Mock 测试 - 无测试步骤")
        steps_result.append({"step": 1, "status": "passed", "action": "navigate"})'''

    return f'''async def run_test(safe) -> dict:
    """Mock — 请配置 OPENAI_API_KEY 以使用 AI 生成"""
    steps_result = []
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

    return {{
        "success": True,
        "message": f"测试通过, {{len(steps_result)}} 步",
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
        sleep({wait_ms / 1000})
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
        driver.save_screenshot("uploads/screenshots/step_{sn}.png")
        steps_result.append({{"step": {sn}, "status": "passed", "action": "screenshot"}})
''')
        else:
            step_lines.append(f'''        # {desc} (未识别的 action: {action}，跳过)
        print(f'[跳过] 步骤{sn}: 未识别的操作 {action}')
        steps_result.append({{"step": {sn}, "status": "passed", "action": "{action}", "note": "未识别"}})
''')

    steps_code = "\n".join(step_lines) if step_lines else '''        print("[执行] Mock 测试 - 无测试步骤")
        steps_result.append({"step": 1, "status": "passed", "action": "navigate"})'''

    return f'''def run_test(driver) -> dict:
    """Mock — 请配置 OPENAI_API_KEY 以使用 AI 生成"""
    steps_result = []
    try:
        print("[执行] Android Mock 测试")

{steps_code}
    except Exception as e:
        return {{
            "success": False,
            "message": str(e),
            "steps": steps_result,
        }}

    return {{
        "success": True,
        "message": f"测试通过, {{len(steps_result)}} 步",
        "steps": steps_result,
    }}
'''
