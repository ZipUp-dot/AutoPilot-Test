"""自愈修复业务逻辑 — 失败上下文捕获 + AI 修复 + 重试执行

触发条件:
  - Playwright 执行抛出异常（TimeoutError / ElementNotFoundError / AssertionError）
  - 自动触发：执行引擎在 healing 阶段自动调用（降低耦合）
  - 手动触发：通过 API 手动对指定步骤启动自愈（调试用）

自愈流程:
  1. 捕获失败上下文（错误信息/截图/DOM快照/步骤日志/原始代码）
  2. 重新抓取当前页面元素（提供最新页面上下文给 AI）
  3. 从 prompts/heal_prompt.txt 加载模板并填充上下文
  4. 调用 AI 生成修复代码（temperature=0.3, timeout=60s）
  5. 使用 CodeValidator.validate 校验修复代码（语法 + 安全）
  6. 保存自愈记录 + 插入 GeneratedCode（is_healed=true）
  7. 在沙箱中重新执行修复代码（最多 3 次重试）
"""

import ast
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.models.heal_record import HealRecord
from app.exceptions import SecurityException
from app.utils.ai_rate_limiter import get_limiter

logger = logging.getLogger("autopilot.heal")

# 共享 AI 限流器（与代码生成 / Vision 共用同一窗口：并发 + 速率）
ai_rate_limiter = get_limiter()

# 快速失败缓存：key = "{step_id}_{error_type}"，value = 连续失败次数
# 同一 step 的同类错误连续失败达到阈值后，跳过后续自愈（不再调用 AI）
_HEAL_FAILURE_CACHE: dict[str, int] = {}
_HEAL_FAILURE_CACHE_MAX_SIZE = 1000

# ── 页面元素提取 JS（与 element_service 共用）──
_EXTRACT_JS = """() => {
    const selectors = [
        'button',
        'input[type="text"]', 'input[type="password"]', 'input[type="email"]', 'input[type="number"]',
        'textarea', 'select',
        'a[href]',
        '[role="button"]', '[role="link"]',
    ];
    const all = document.querySelectorAll(selectors.join(','));
    const seen = new Set();
    const result = [];

    all.forEach((el, i) => {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        if (el.offsetParent === null) return;

        const uid = el.outerHTML ? el.outerHTML.substring(0, 80) : el.tagName + i;
        if (seen.has(uid)) return;
        seen.add(uid);

        const tag = el.tagName.toLowerCase();
        let element_type = tag;
        if (tag === 'input') element_type = el.type || 'text';
        if (tag === 'a') element_type = 'link';

        result.push({
            index: i,
            tag: tag,
            element_type: element_type,
            id: el.id || null,
            name: el.getAttribute('name') || null,
            className: el.className || null,
            textContent: (el.textContent || '').trim() || null,
            placeholder: el.getAttribute('placeholder') || null,
            type: el.getAttribute('type') || null,
            href: el.getAttribute('href') || null,
            role: el.getAttribute('role') || null,
            dataTestid: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || null,
            isVisible: true,
            boundingBox: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            },
        });
    });
    return result;
}"""

# 动态 class 模式
_DYNAMIC_CLASS = re.compile(
    r'(css-[a-z0-9]+|_[a-zA-Z0-9]{6,}|[a-z]+-[a-f0-9]{6,}|sc-[a-zA-Z]+$)'
)


@dataclass
class HealResult:
    """自愈结果"""
    heal_id: int = 0
    healed_code: str = ""
    retry_status: str = "pending"  # pending / retrying / success / failed
    retry_count: int = 0
    error_message: str = ""


class HealService:
    """失败步骤自动修复——调用 AI 修补选择器 + 重新执行"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 主入口 — 自动触发（返回 bool）
    # ═══════════════════════════════════════════════

    async def try_heal(
        self,
        execution_id: int,
        step: ExecutionStep,
        page,
        project_id: int,
        max_retries: int = 3,
        platform: str = "web",
    ) -> bool:
        """尝试修复单个失败步骤（由执行引擎自动调用）

        Args:
            platform: "web" 或 "android"，决定 Prompt 模板、代码校验、重试执行分支

        Returns:
            True 表示修复成功并重试通过
        """
        case_id = step.case_id
        step_index = step.step_index
        logger.info("开始自愈: execution_id=%s case=%s step=%s platform=%s", execution_id, case_id, step_index, platform)

        # 0a. 页面健康检查：目标环境不可达时跳过自愈，避免无意义地调用 AI
        if not await self._check_env_reachable(page, project_id, platform=platform):
            logger.error("目标环境不可达，跳过自愈: execution_id=%s step_id=%s", execution_id, step.id)
            return False

        # 0b. 快速失败：同一 step 的同类错误已连续失败达到阈值，直接跳过
        error_type = self._classify_error(step.error_message or "")
        cache_key = f"{step.id}_{error_type}"
        if _HEAL_FAILURE_CACHE.get(cache_key, 0) >= settings.HEAL_MAX_RETRY_SAME_ERROR:
            logger.warning(
                "步骤 %s 错误类型 %s 已连续失败 %d 次，跳过自愈（快速失败）",
                step.id, error_type, settings.HEAL_MAX_RETRY_SAME_ERROR,
            )
            return False

        # 1. 捕获失败上下文
        error_ctx = await self._capture_failure_context(step, page, platform=platform)

        # 2. 获取原始代码
        original_code = self._get_original_code(case_id)

        # 3. 逐次重试
        for attempt in range(1, max_retries + 1):
            logger.info("自愈第 %s/%s 次: step_id=%s", attempt, max_retries, step.id)

            # 4. 构建修复 Prompt
            prompt = self._build_heal_prompt(error_ctx, original_code, step, platform=platform)

            # 5. 调用 AI
            try:
                healed_code = self._call_heal_ai(prompt, platform=platform)
            except Exception as e:
                logger.error("AI 修复调用失败(第%s次): %s", attempt, e)
                continue

            if not healed_code or "UNABLE_TO_HEAL" in healed_code:
                logger.warning("AI 返回无法修复: %s", healed_code[:100] if healed_code else "空响应")
                break

            # 6. 提取 + 校验代码
            healed_code = self._extract_code(healed_code)
            validation_error = self._validate_healed(healed_code, platform=platform)
            if validation_error:
                logger.warning("修复代码校验失败(第%s次): %s", attempt, validation_error)
                continue

            # 7. 保存自愈记录
            heal_record = self._save_heal_record(
                step.id, original_code, error_ctx, healed_code, prompt, attempt
            )

            # 8. 重新执行修复后的代码
            success = await self._retry_execution(
                page, healed_code, step, execution_id, case_id, platform=platform
            )

            if success:
                self._update_heal_record(heal_record.id, "success")
                # 插入修复后的代码到 generated_codes（is_healed=true）
                self._insert_healed_code(case_id, healed_code, prompt)
                # 自愈成功 → 清除快速失败计数
                _HEAL_FAILURE_CACHE.pop(cache_key, None)
                logger.info("自愈成功: step_id=%s 第%s次", step.id, attempt)
                return True
            else:
                self._update_heal_record(heal_record.id, "failed")
                logger.warning("自愈重试失败: step_id=%s 第%s次", step.id, attempt)

        # 全部失败 → 记录快速失败计数 + 标记最终失败
        self._track_heal_failure(cache_key)
        step.status = "failed"
        self._db.commit()
        logger.error("自愈全部失败(%s次): step_id=%s", max_retries, step.id)
        return False

    # ═══════════════════════════════════════════════
    # 主入口 — 手动触发（返回 HealResult）
    # ═══════════════════════════════════════════════

    async def try_heal_manual(
        self,
        execution_id: int,
        step: ExecutionStep,
        page,
        project_id: int,
        max_retries: int = 3,
        platform: str = "web",
    ) -> HealResult:
        """手动触发自愈，返回完整 HealResult 供 API 响应

        Returns:
            HealResult(heal_id, healed_code, retry_status, retry_count)
        """
        case_id = step.case_id
        logger.info("手动自愈: execution_id=%s case=%s step=%s platform=%s", execution_id, case_id, step.step_index, platform)

        # 页面健康检查：目标环境不可达时跳过自愈
        if not await self._check_env_reachable(page, project_id, platform=platform):
            logger.error("目标环境不可达，跳过手动自愈: execution_id=%s step_id=%s", execution_id, step.id)
            return HealResult(
                heal_id=0, retry_status="failed", retry_count=0,
                error_message="目标环境不可达，跳过自愈",
            )

        # 快速失败：同一 step 的同类错误已连续失败达到阈值
        error_type = self._classify_error(step.error_message or "")
        cache_key = f"{step.id}_{error_type}"
        if _HEAL_FAILURE_CACHE.get(cache_key, 0) >= settings.HEAL_MAX_RETRY_SAME_ERROR:
            logger.warning(
                "步骤 %s 错误类型 %s 已连续失败 %d 次，跳过自愈（快速失败）",
                step.id, error_type, settings.HEAL_MAX_RETRY_SAME_ERROR,
            )
            return HealResult(
                heal_id=0, retry_status="failed", retry_count=0,
                error_message=f"错误类型 {error_type} 已连续失败 {settings.HEAL_MAX_RETRY_SAME_ERROR} 次，跳过自愈",
            )

        error_ctx = await self._capture_failure_context(step, page, platform=platform)
        original_code = self._get_original_code(case_id)
        last_heal_record = None
        last_healed_code = ""

        for attempt in range(1, max_retries + 1):
            logger.info("手动自愈第 %s/%s 次: step_id=%s", attempt, max_retries, step.id)

            prompt = self._build_heal_prompt(error_ctx, original_code, step, platform=platform)

            try:
                healed_code = self._call_heal_ai(prompt, platform=platform)
            except Exception as e:
                return HealResult(
                    heal_id=0, retry_status="failed", retry_count=attempt,
                    error_message=f"AI 调用失败: {str(e)[:200]}",
                )

            if not healed_code or "UNABLE_TO_HEAL" in healed_code:
                return HealResult(
                    heal_id=0, retry_status="failed", retry_count=attempt,
                    error_message="AI 返回无法修复",
                )

            healed_code = self._extract_code(healed_code)
            validation_error = self._validate_healed(healed_code, platform=platform)
            if validation_error:
                last_healed_code = healed_code
                continue

            heal_record = self._save_heal_record(
                step.id, original_code, error_ctx, healed_code, prompt, attempt
            )
            last_heal_record = heal_record
            last_healed_code = healed_code

            success = await self._retry_execution(
                page, healed_code, step, execution_id, case_id, platform=platform
            )

            if success:
                self._update_heal_record(heal_record.id, "success")
                self._insert_healed_code(case_id, healed_code, prompt)
                _HEAL_FAILURE_CACHE.pop(cache_key, None)
                return HealResult(
                    heal_id=heal_record.id,
                    healed_code=healed_code,
                    retry_status="success",
                    retry_count=attempt,
                )
            else:
                self._update_heal_record(heal_record.id, "failed")

        # 全部失败 → 记录快速失败计数
        self._track_heal_failure(cache_key)
        step.status = "failed"
        self._db.commit()
        return HealResult(
            heal_id=last_heal_record.id if last_heal_record else 0,
            healed_code=last_healed_code,
            retry_status="failed",
            retry_count=max_retries,
            error_message=f"自愈全部失败({max_retries}次)",
        )

    # ═══════════════════════════════════════════════
    # 辅助：环境健康检查 + 快速失败计数
    # ═══════════════════════════════════════════════

    async def _check_env_reachable(self, page, project_id: int, platform: str = "web") -> bool:
        """自愈前检查目标环境是否可达

        目标网站/设备不可达时，AI 修复无意义（问题不在代码而在环境），
        直接跳过自愈，避免无底线调用 AI 烧 Token。

        Returns:
            True 表示环境可达，可继续自愈
        """
        try:
            if platform == "android":
                # Appium driver 存活检查
                _ = page.page_source
                return True

            # Web：尝试访问项目目标 URL
            from app.models.project import Project
            from app.utils.url_policy import validate_target_url
            project = self._db.query(Project).filter(Project.id == project_id).first()
            target_url = (project.target_url or "").strip() if project else ""
            if not target_url:
                return True  # 无目标 URL 则不拦截

            # SSRF 入口校验：非法目标 URL 视为环境不可达，跳过自愈
            try:
                config_json = json.loads(project.config_json) if project and project.config_json else None
            except (TypeError, ValueError):
                config_json = None
            if validate_target_url(target_url, config_json=config_json):
                logger.error("目标 URL 校验失败，跳过自愈: %s", target_url)
                return False

            await page.goto(target_url, wait_until="domcontentloaded", timeout=8000)
            return True
        except Exception as e:
            logger.error("目标环境不可达: %s", str(e)[:200])
            return False

    @staticmethod
    def _track_heal_failure(cache_key: str) -> None:
        """记录一次自愈失败（含缓存容量保护）"""
        # 缓存超限时清空（防止进程长期运行导致内存增长）
        if len(_HEAL_FAILURE_CACHE) >= _HEAL_FAILURE_CACHE_MAX_SIZE:
            _HEAL_FAILURE_CACHE.clear()
        _HEAL_FAILURE_CACHE[cache_key] = _HEAL_FAILURE_CACHE.get(cache_key, 0) + 1

    # ═══════════════════════════════════════════════
    # 失败上下文捕获
    # ═══════════════════════════════════════════════

    async def _capture_failure_context(
        self, step: ExecutionStep, page, platform: str = "web"
    ) -> dict:
        """捕获失败步骤的完整上下文"""
        ctx = {
            "action": step.action or "",
            "target": step.target_selector or "",
            "value": step.input_value or "",
            "error_type": self._classify_error(step.error_message or ""),
            "error_message": (step.error_message or "未知错误")[:500],
            "screenshot_before": step.screenshot_before or "",
            "screenshot_after": step.screenshot_after or "",
        }

        if platform == "android":
            # Android: page_source XML + exception_type + selector_type
            ctx["exception_type"] = step.exception_type or ""
            ctx["selector_type"] = ""
            try:
                source = page.page_source  # page is Appium driver for Android
                ctx["page_source"] = source[:100000]
                # 提取可见元素
                visible = _parse_android_elements(source)
                ctx["visible_elements"] = _format_android_elements(visible)
            except Exception as e:
                logger.warning("Android 上下文捕获失败: %s", e)
                ctx["page_source"] = "(无法获取 Page Source)"
                ctx["visible_elements"] = "(无法获取页面元素)"
        else:
            # Web: DOM 快照（截断至 100KB）
            try:
                dom = await page.content()
                ctx["dom_snapshot"] = dom[:100000]
            except Exception as e:
                logger.warning("DOM 快照获取失败: %s", e)
                ctx["dom_snapshot"] = "(无法获取 DOM 快照)"

            # 重新抓取页面元素
            try:
                elements = await self._recrawl_elements(page)
                ctx["elements_list"] = self._format_elements_compact(elements)
            except Exception as e:
                logger.warning("元素重抓失败: %s", e)
                ctx["elements_list"] = "(无法获取页面元素)"

        return ctx

    @staticmethod
    def _classify_error(error_msg: str) -> str:
        """分类错误类型

        支持 Web (Playwright) 和 Android (Appium) 异常类型。
        优先匹配更具体的异常类型，再回退到通用匹配。
        """
        msg_lower = error_msg.lower()
        # Appium 异常（优先匹配）
        if "staleelementreferenceexception" in msg_lower or "stale element" in msg_lower:
            return "StaleElementError"
        if "nosuchelementexception" in msg_lower:
            return "ElementNotFoundError"
        if "timeoutexception" in msg_lower:
            return "TimeoutError"
        if "webdriverexception" in msg_lower:
            return "DriverError"
        # Web 异常
        if "timeout" in msg_lower:
            return "TimeoutError"
        if "resolve" in msg_lower or "locator" in msg_lower or "element" in msg_lower:
            return "ElementNotFoundError"
        if "assert" in msg_lower or "expect" in msg_lower:
            return "AssertionError"
        if "navigation" in msg_lower or "net::" in msg_lower:
            return "NavigationError"
        return "UnknownError"

    def _get_original_code(self, case_id: int) -> str:
        """获取用例最新原始代码"""
        gen = (
            self._db.query(GeneratedCode)
            .filter(GeneratedCode.case_id == case_id, GeneratedCode.is_valid == 1)
            .order_by(GeneratedCode.created_at.desc())
            .first()
        )
        return gen.code_content if gen else ""

    # ═══════════════════════════════════════════════
    # 页面元素重抓
    # ═══════════════════════════════════════════════

    async def _recrawl_elements(self, page) -> list[dict]:
        """重新抓取当前页面元素，生成选择器"""
        raw_elements = await page.evaluate(_EXTRACT_JS)
        elements = []
        for raw in raw_elements:
            selector = await self._generate_selector(page, raw)
            elements.append({
                "selector": selector,
                "tag": raw.get("tag", ""),
                "text": (raw.get("textContent") or "")[:80],
                "type": raw.get("element_type", raw.get("tag", "")),
                "el_id": raw.get("id") or "",
                "name": raw.get("name") or "",
                "placeholder": raw.get("placeholder") or "",
                "className": raw.get("className") or "",
                "dataTestid": raw.get("dataTestid") or "",
            })
        return elements

    async def _generate_selector(self, page, raw: dict) -> str:
        """按优先级生成选择器（与 ElementService 逻辑一致，但不写 DB）"""
        tag = raw.get("tag", "")
        el_id = raw.get("id", "")
        name_attr = raw.get("name", "")
        placeholder = raw.get("placeholder", "")
        text = (raw.get("textContent") or "")[:50].strip()
        className = raw.get("className", "")
        data_testid = raw.get("dataTestid", "")

        if data_testid:
            sel = f'[data-testid="{data_testid}"]'
            if await self._is_unique(page, sel):
                return sel
        if el_id:
            sel = f"#{_css_escape(el_id)}"
            if await self._is_unique(page, sel):
                return sel
        if name_attr:
            sel = f'[name="{name_attr}"]'
            if await self._is_unique(page, sel):
                return sel
        if placeholder:
            sel = f'{tag}[placeholder="{placeholder}"]'
            if await self._is_unique(page, sel):
                return sel
        stable_classes = _filter_stable_classes(className.split()) if className else []
        if stable_classes:
            sel = f"{tag}.{'.'.join(stable_classes[:2])}"
            if await self._is_unique(page, sel):
                return sel
        if text:
            sel = f'{tag}:has-text("{text}")'
            if await self._is_unique(page, sel):
                return sel
        idx = raw.get("index", 0)
        return f"{tag}:nth-child({idx + 1})" if idx >= 0 else tag

    @staticmethod
    async def _is_unique(page, selector: str) -> bool:
        try:
            count = await page.evaluate(
                """(sel) => document.querySelectorAll(sel).length""", selector
            )
            return count == 1
        except Exception:
            return False

    @staticmethod
    def _format_elements_compact(elements: list[dict]) -> str:
        if not elements:
            return "（无可用元素）"
        lines = []
        for el in elements:
            parts = [f"[{el['type']}] tag={el['tag']}"]
            if el["el_id"]:
                parts.append(f"id={el['el_id']}")
            if el["name"]:
                parts.append(f"name={el['name']}")
            if el["placeholder"]:
                parts.append(f"placeholder={el['placeholder']}")
            if el["text"]:
                parts.append(f'text="{el["text"]}"')
            if el["dataTestid"]:
                parts.append(f"data-testid={el['dataTestid']}")
            parts.append(f"selector={el['selector']}")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    # ═══════════════════════════════════════════════
    # Prompt 构建 + AI 调用
    # ═══════════════════════════════════════════════

    def _build_heal_prompt(
        self, error_ctx: dict, original_code: str, step: ExecutionStep, platform: str = "web"
    ) -> str:
        """从文件加载 Prompt 模板并填充上下文（每次读取，支持热更新）"""
        if platform == "android":
            prompt_name = "heal_prompt_android.txt"
        else:
            prompt_name = "heal_prompt.txt"
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "prompts", prompt_name,
        )
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read()
        else:
            template = (
                "修复以下测试代码中的失败步骤。\n"
                "【原始代码】\n{original_code}\n"
                "【失败步骤】\nStep {failed_step_index}: {failed_action}\nTarget: {failed_target}\n"
                "【错误信息】\n{error_message}\n"
                "请修复代码。"
            )

        if platform == "android":
            return template.format(
                original_code=original_code,
                failed_step_index=step.step_index,
                failed_action=step.action or "",
                failed_target=step.target_selector or "",
                selector_type=error_ctx.get("selector_type", ""),
                error_message=error_ctx.get("error_message", ""),
                exception_type=error_ctx.get("exception_type", ""),
                page_source=error_ctx.get("page_source", ""),
                screenshot_before=error_ctx.get("screenshot_before", ""),
                screenshot_after=error_ctx.get("screenshot_after", ""),
                visible_elements=error_ctx.get("visible_elements", ""),
            )
        return template.format(
            original_code=original_code,
            failed_step_index=step.step_index,
            failed_action=step.action or "",
            failed_target=step.target_selector or "",
            error_message=error_ctx.get("error_message", ""),
            dom_snapshot=error_ctx.get("dom_snapshot", ""),
            screenshot_before=error_ctx.get("screenshot_before", ""),
            screenshot_after=error_ctx.get("screenshot_after", ""),
            elements_list=error_ctx.get("elements_list", ""),
        )

    def _call_heal_ai(self, prompt: str, platform: str = "web") -> str:
        """调用 OpenAI API 生成修复代码（temperature=0.3, timeout=60s）

        受全局限流器约束：并发（Semaphore） + 每分钟速率（AI_RATE_LIMIT），
        超限抛出异常由调用方捕获（跳过本次自愈，不调用 AI）。
        """
        # Mock 模式不消耗限流额度
        if not settings.OPENAI_API_KEY:
            return self._mock_heal_response(platform=platform)

        # 熔断：超出每分钟调用上限则跳过
        if not ai_rate_limiter.acquire():
            raise Exception(
                f"AI API 调用熔断：每分钟最多 {settings.AI_RATE_LIMIT} 次，请稍后重试"
            )
        # 并发控制：与代码生成共用同一并发槽
        if not ai_rate_limiter.acquire_slot():
            raise Exception("AI 并发调用已满（排队超时），请稍后重试")

        if platform == "android":
            system_msg = "你是 Appium Android 测试修复专家。只返回完整的 def run_test(driver) Python 代码，不含 markdown 标记和解释。"
        else:
            system_msg = "你是 Playwright 测试修复专家。只返回完整的 async def run_test(safe) Python 代码，使用 safe.goto / safe.click 等受控 API，不含 markdown 标记和解释。"

        last_error = None
        try:
            for attempt in range(3):
                try:
                    with httpx.Client(timeout=60.0) as client:
                        response = client.post(
                            f"{settings.OPENAI_BASE_URL}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": settings.OPENAI_MODEL,
                                "messages": [
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": prompt},
                                ],
                                "temperature": 0.3,
                                "max_tokens": 4096,
                            },
                        )
                        response.raise_for_status()
                        body = response.json()
                        return body["choices"][0]["message"]["content"]
                except Exception as e:
                    last_error = e
                    if attempt < 2:
                        time.sleep(2 ** attempt)
            raise Exception(f"AI 调用失败(已重试3次): {last_error}")
        finally:
            ai_rate_limiter.release_slot()

    @staticmethod
    def _mock_heal_response(platform: str = "web") -> str:
        """Mock 模式下的自愈响应"""
        if platform == "android":
            return '''def run_test(driver):
    """Mock 自愈代码 — 请配置 OPENAI_API_KEY"""
    steps_result = []
    from datetime import datetime
    start_time = datetime.now()
    try:
        print("[自愈] Mock — 请配置 OPENAI_API_KEY")
        steps_result.append({"step": 1, "status": "passed", "action": "healed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}

    duration = (datetime.now() - start_time).total_seconds()
    return {"success": True, "message": f"自愈通过, {duration:.1f}s", "steps": steps_result}
'''
        return '''import asyncio
from datetime import datetime


async def run_test(safe) -> dict:
    """Mock 自愈代码 — 请配置 OPENAI_API_KEY"""
    steps_result = []
    start_time = datetime.now()
    try:
        print("[自愈] Mock — 请配置 OPENAI_API_KEY")
        await safe.goto("https://example.com")
        await safe.wait(1000)
        steps_result.append({"step": 1, "status": "passed", "action": "healed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}

    duration = (datetime.now() - start_time).total_seconds()
    return {"success": True, "message": f"自愈通过, {duration:.1f}s", "steps": steps_result}
'''

    # ═══════════════════════════════════════════════
    # 代码校验（使用 code_validator）
    # ═══════════════════════════════════════════════

    @staticmethod
    def _extract_code(raw: str) -> str:
        """从 AI 响应中提取纯 Python 代码"""
        code = raw.strip()
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

    @staticmethod
    def _validate_healed(code: str, platform: str = "web") -> Optional[str]:
        """校验修复后的代码（使用 code_validator）

        Args:
            platform: "web" 或 "android"，决定校验规则

        Returns:
            错误消息，如果通过则返回 None
        """
        from app.utils.code_validator import CodeValidator
        return CodeValidator.validate(code, platform=platform)

    # ═══════════════════════════════════════════════
    # 重试执行
    # ═══════════════════════════════════════════════

    async def _retry_execution(
        self,
        page,
        code: str,
        step: ExecutionStep,
        execution_id: int,
        case_id: int,
        platform: str = "web",
    ) -> bool:
        """在沙箱中执行修复后的代码"""
        if platform == "android":
            return self._retry_execution_sync(page, code, step, execution_id, case_id)

        from app.utils.code_injector import CodeInjector
        from app.utils.safe_playwright import SafePlaywright
        from app.services.playwright_service import _build_namespace, _MonitorHooks

        # AST 注入监控钩子
        try:
            code = CodeInjector.inject(code)
        except SecurityException:
            logger.warning("自愈代码注入失败，使用原始代码")

        hooks = _MonitorHooks(self._db, execution_id, case_id, page)
        safe = SafePlaywright(page)
        namespace = _build_namespace(safe, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            logger.error("自愈代码 exec 失败: step_id=%s, %s", step.id, e)
            return False

        run_test = namespace.get("run_test")
        if not run_test:
            logger.error("自愈代码缺少 run_test: step_id=%s", step.id)
            return False

        try:
            result = await asyncio.wait_for(run_test(safe), timeout=120.0)
            success = result.get("success", False) if isinstance(result, dict) else False
            if success:
                step.status = "success"
                step.log_output = f"[HEALED] step {step.step_index}: 自愈修复成功"
                step.error_message = None
                self._db.commit()
            return success
        except asyncio.TimeoutError:
            logger.error("自愈执行超时: step_id=%s", step.id)
            return False
        except Exception as e:
            logger.error("自愈执行异常: step_id=%s, %s", step.id, e)
            step.error_message = f"自愈重试失败: {str(e)[:500]}"
            self._db.commit()
            return False

    def _retry_execution_sync(
        self,
        page,
        code: str,
        step: ExecutionStep,
        execution_id: int,
        case_id: int,
    ) -> bool:
        """在沙箱中执行修复后的代码（Android 同步版）"""
        from app.utils.appium_code_injector import AppiumCodeInjector
        from app.services.appium_service import _build_sync_namespace, _SyncMonitorHooks

        # AST 注入监控钩子
        try:
            code = AppiumCodeInjector.inject(code)
        except SecurityException:
            logger.warning("Android 自愈代码注入失败，使用原始代码")

        hooks = _SyncMonitorHooks(self._db, execution_id, case_id, page)
        namespace = _build_sync_namespace(page, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            logger.error("Android 自愈代码 exec 失败: step_id=%s, %s", step.id, e)
            return False

        run_test = namespace.get("run_test")
        if not run_test:
            logger.error("Android 自愈代码缺少 run_test: step_id=%s", step.id)
            return False

        try:
            result = run_test(page)
            success = result.get("success", False) if isinstance(result, dict) else False
            if success:
                step.status = "success"
                step.log_output = f"[HEALED] step {step.step_index}: 自愈修复成功"
                step.error_message = None
                self._db.commit()
            return success
        except Exception as e:
            logger.error("Android 自愈执行异常: step_id=%s, %s", step.id, e)
            step.error_message = f"自愈重试失败: {str(e)[:500]}"
            self._db.commit()
            return False

    # ═══════════════════════════════════════════════
    # 自愈记录 + 生成代码管理
    # ═══════════════════════════════════════════════

    def _save_heal_record(
        self,
        step_id: int,
        original_code: str,
        error_ctx: dict,
        healed_code: str,
        prompt: str,
        retry_count: int,
    ) -> HealRecord:
        """保存自愈记录到数据库"""
        attempts = [{
            "attempt": retry_count,
            "generated_code": healed_code[:5000],
            "status": "retrying",
            "error": "",
            "created_at": datetime.utcnow().isoformat(),
        }]
        record = HealRecord(
            execution_step_id=step_id,
            original_code=original_code[:3000],
            error_context=json.dumps(error_ctx, ensure_ascii=False),
            healed_code=healed_code[:5000],
            heal_prompt=prompt[:5000],
            retry_status="retrying",
            retry_count=retry_count,
            attempts=json.dumps(attempts, ensure_ascii=False),
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        return record

    def _update_heal_record(self, record_id: int, status: str) -> None:
        """更新自愈记录状态 + 追加 attempt 结果"""
        record = self._db.query(HealRecord).filter(HealRecord.id == record_id).first()
        if not record:
            return
        record.retry_status = status

        # 更新 attempts 最后一个 entry 的状态
        try:
            attempts = json.loads(record.attempts) if record.attempts else []
        except (json.JSONDecodeError, TypeError):
            attempts = []

        if attempts:
            last = attempts[-1]
            last["status"] = status
            if status == "failed":
                from app.models.execution_step import ExecutionStep
                step = self._db.query(ExecutionStep).filter(
                    ExecutionStep.id == record.execution_step_id
                ).first()
                last["error"] = (step.error_message or "")[:500] if step else ""
            record.attempts = json.dumps(attempts, ensure_ascii=False)

        self._db.commit()

    def _insert_healed_code(self, case_id: int, healed_code: str, prompt: str) -> None:
        """将修复后的代码插入 generated_codes（is_healed=true）"""
        gen_code = GeneratedCode(
            case_id=case_id,
            code_content=healed_code,
            code_language="python",
            generation_prompt=prompt,
            ai_model=settings.OPENAI_MODEL,
            is_valid=1,
            is_healed=1,
        )
        self._db.add(gen_code)
        self._db.commit()


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def _css_escape(value: str) -> str:
    return value.replace(":", "\\:").replace(".", "\\.").replace("#", "\\#")


def _filter_stable_classes(classes: list[str]) -> list[str]:
    result = []
    for c in classes:
        if not c or len(c) < 2:
            continue
        if _DYNAMIC_CLASS.search(c):
            continue
        if c.startswith("ant-") and len(c) > 20:
            continue
        result.append(c)
    return result


# ═══════════════════════════════════════════════
# Android 元素解析
# ═══════════════════════════════════════════════

_ANDROID_ELEMENT_PATTERN = re.compile(
    r'<(android\.\w+\.\w+)\s+([^>]*)>'
)

_ANDROID_ATTR_PATTERN = re.compile(r'(\w+)="([^"]*)"')


def _parse_android_elements(page_source: str) -> list[dict]:
    """从 Android page_source XML 中提取可交互元素"""
    if not page_source or page_source == "(无法获取 Page Source)":
        return []
    elements = []
    for match in _ANDROID_ELEMENT_PATTERN.finditer(page_source):
        tag = match.group(1)
        attrs_str = match.group(2)
        attrs = {}
        for attr_match in _ANDROID_ATTR_PATTERN.finditer(attrs_str):
            attrs[attr_match.group(1)] = attr_match.group(2)

        # 只保留可交互或包含文本的元素
        clickable = attrs.get("clickable", "false")
        enabled = attrs.get("enabled", "true")
        text = attrs.get("text", "").strip()
        content_desc = attrs.get("content-desc", "").strip()
        if clickable != "true" and not text and not content_desc:
            continue

        class_name = tag.split(".")[-1] if "." in tag else tag
        resource_id = attrs.get("resource-id", "")
        bounds = attrs.get("bounds", "")
        package = attrs.get("package", "")

        elements.append({
            "resource_id": resource_id,
            "content_desc": content_desc,
            "text": text[:80] if text else "",
            "class_name": class_name,
            "bounds": bounds,
            "enabled": enabled,
            "clickable": clickable,
            "package": package,
        })
    return elements


def _format_android_elements(elements: list[dict]) -> str:
    """格式化 Android 元素列表为可读文本"""
    if not elements:
        return "（无可用元素）"
    lines = []
    for el in elements:
        parts = [f"[{el['class_name']}]"]
        if el["resource_id"]:
            parts.append(f"resource-id={el['resource_id']}")
        if el["content_desc"]:
            parts.append(f"content-desc={el['content_desc']}")
        if el["text"]:
            parts.append(f'text="{el["text"]}"')
        if el["bounds"]:
            parts.append(f"bounds={el['bounds']}")
        if el["clickable"] == "true":
            parts.append("clickable")
        lines.append("  ".join(parts))
    return "\n".join(lines)
