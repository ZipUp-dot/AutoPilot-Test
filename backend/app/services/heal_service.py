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
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.models.heal_record import HealRecord
from app.exceptions import SecurityException
from app.utils.ai_rate_limiter import get_limiter
from app.services.element_extractor import (
    extract_elements as _extract_elements_shared,
    generate_selector as _generate_selector_shared,
    is_unique as _is_unique_shared,
)

logger = logging.getLogger("autopilot.heal")

# 共享 AI 限流器（与代码生成 / Vision 共用同一窗口：并发 + 速率）
ai_rate_limiter = get_limiter()

# 快速失败缓存：key = "{step_id}_{error_type}"，value = 连续失败次数
# 同一 step 的同类错误连续失败达到阈值后，跳过后续自愈（不再调用 AI）
_HEAL_FAILURE_CACHE: dict[str, int] = {}
_HEAL_FAILURE_CACHE_MAX_SIZE = 1000


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

        # 2. 获取原始代码（冻结的活跃代码）
        original_code = self._get_original_code(execution_id, case_id)

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
        original_code = self._get_original_code(execution_id, case_id)
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

    def _get_original_code(self, execution_id: int, case_id: int) -> str:
        """获取该 Execution 下冻结的活跃原始代码（统一走 ExecutionCodeResolver）"""
        from app.services.execution_code_resolver import ExecutionCodeResolver
        gen = ExecutionCodeResolver(self._db).get_active_code(execution_id, case_id)
        return gen.code_content if gen else ""

    # ═══════════════════════════════════════════════
    # 页面元素重抓
    # ═══════════════════════════════════════════════

    async def _recrawl_elements(self, page) -> list[dict]:
        """重新抓取当前页面元素，生成选择器（P1-2：走 element_extractor 共享实现）"""
        raw_elements = await _extract_elements_shared(page)
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
        """P1-2：单一实现来源 element_extractor（与 ElementService 同源，但不写 DB）"""
        return await _generate_selector_shared(page, raw)

    @staticmethod
    async def _is_unique(page, selector: str) -> bool:
        """P1-2：唯一性检查 Playwright-native（page.locator().count()）"""
        return await _is_unique_shared(page, selector)

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
        """Mock 模式下的自愈响应（P1-3：无 import、无 datetime 自算耗时）"""
        if platform == "android":
            return '''def run_test(driver):
    """Mock 自愈代码 — 请配置 OPENAI_API_KEY"""
    steps_result = []
    try:
        print("[自愈] Mock — 请配置 OPENAI_API_KEY")
        steps_result.append({"step": 1, "status": "passed", "action": "healed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}

    return {"success": True, "message": "自愈通过", "steps": steps_result}
'''
        return '''async def run_test(safe) -> dict:
    """Mock 自愈代码 — 请配置 OPENAI_API_KEY"""
    steps_result = []
    try:
        print("[自愈] Mock — 请配置 OPENAI_API_KEY")
        await safe.goto("https://example.com")
        await safe.wait(1000)
        steps_result.append({"step": 1, "status": "passed", "action": "healed"})
    except Exception as e:
        return {"success": False, "message": str(e), "steps": steps_result}

    return {"success": True, "message": "自愈通过", "steps": steps_result}
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
            result = run_test(namespace["driver"])
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
        # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 HealRecord 写入
        from app.models.execution_step import ExecutionStep as _ES
        _step = self._db.query(_ES).filter(_ES.id == step_id).first()
        if _step is not None:
            from app.utils.seal_guard import guard_not_sealed
            guard_not_sealed(self._db, _step.execution_id)

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
# P0-8 Case 级 HealRound（Round guard / Candidate 隔离 / Finalization / Recovery 收敛）
#
# 触发单位是【Case】而非 Step（禁止逐 step 触发 Heal）：
#   - claim()：per-execution 串行化 + stop 检查 + UNIQUE(execution_id,case_id,round_no)
#     作为 DB 级竞态（MySQL 同一临界区 SELECT ... FOR UPDATE 锁 execution 行，
#     SQLite 由 process-local per-execution lock 串行化）；Auto/Manual 统一走本服务。
#   - 每 candidate：先 CodeValidator；通过后【整 Case rerun】（内存步骤证据进
#     attempts[]，绝不写 canonical ExecutionStep）；第一个『Validator 通过且整 Case
#     rerun Resolver=success』的 candidate 立即成为 winning candidate。
#   - Finalization 两阶段：Phase A【成功事实落盘】独立小事务 COMMIT（finalizing）；
#     Phase B【Finalization Transaction】进入 per-execution 临界区后重新读取最新
#     stop_requested_at（NULL 才允许成功 Finalization），缺一不可地更新
#     GeneratedCode + healed_code_id + retry_status + runtime_state + Step 状态。
#   - 原始代码来源：ExecutionCodeResolver 读 runtime_state.active_code_id；
#     禁止 get_latest_code / _get_original_code。
# HealService（旧逐 step 入口）保留供既有契约测试/兼容，生产触发统一走 HealRoundService。
# ═══════════════════════════════════════════════

# error_type 允许值（七个，钉死，缺一不可）
HEAL_ERROR_TYPES = frozenset({
    "heal_finalization_error", "validation_error", "worker_failed",
    "deadline_exceeded", "ai_request_failed", "ai_schema_error", "heal_exhausted",
})

# retry_status 值域（P0-8）
HEAL_RETRY_STATUSES = frozenset({
    "pending", "retrying", "finalizing", "success", "failed", "cancelled_by_recovery",
})

# Rerun Failure Precedence：仅这些为【明确基础设施原因】（Round 按基础设施故障收口）
_INFRA_RERUN_ERRORS = frozenset({"worker_failed", "deadline_exceeded"})

# 每 Execution 串行化锁（SQLite 进程内 claim 竞态；MySQL 另有 SELECT FOR UPDATE）
# P0-9：与 Stop / Recovery / ExecutionFinalizer / queued→running 条件启动共用
# execution_state 的统一 per-execution lock（同一线性化边界）。

# Phase B 瞬时事务失败可重试上限（代码级硬编码；Frozen Spec 未定义次数，不得凭空新增配置参数）
_PHASE_B_MAX_RETRY = 3


def _get_exec_lock(execution_id: int) -> threading.Lock:
    """返回该 Execution 的 process-local 串行化锁（与全局线性化边界共用，防 dict 并发扩容）"""
    from app.services.execution_state import get_execution_lock
    return get_execution_lock(execution_id)


@dataclass
class ClaimOutcome:
    """claim() 结果：ok=False 时 reason 承载拒绝原因（stop_requested / 已存在 / 并发冲突）"""
    ok: bool = False
    record: Optional[Any] = None
    reason: str = ""


@dataclass
class RerunResult:
    """候选代码整 Case rerun 的内存结果（步骤证据只进 attempts[]，不落 canonical Step）"""
    ok: bool = False
    error_type: Optional[str] = None      # worker_failed / deadline_exceeded / business_failure / case_rerun_failed
    error_message: str = ""
    step_results: dict = field(default_factory=dict)  # {step_no: {status, error_type, exception_type, ...}}


@dataclass
class HealRoundResult:
    """一次 Case 级 HealRound 的结果（供 _start_healing / Manual 路由消费）"""
    heal_id: int = 0
    retry_status: str = "pending"          # 终态：success / failed / cancelled_by_recovery / skipped(未触发)
    error_type: Optional[str] = None       # 仅 failed 必填（七值之一）
    healed_code_id: Optional[int] = None
    healed_code: str = ""
    error_message: str = ""
    rounds_consumed: bool = True           # False = 未触发 Heal（无 failed step / 无活跃代码）
    upgrade_execution_failed: bool = False  # 真实基础设施故障 → 该 Case 升级 execution_failed


class HealRoundService:
    """Case 级 HealRound（P0-8）—— 自愈统一入口（Auto / Manual）

    复用 HealService 的上下文捕获 / Prompt 构建 / AI 调用 / Mock helpers（组合而非复制）。
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._heal_svc = HealService(db)

    # ═══════════════════════════════════════════════
    # 主入口 — 单个 Case 的一次 HealRound
    # ═══════════════════════════════════════════════

    async def heal_case(
        self,
        execution_id: int,
        case_id: int,
        project_id: int,
        page,
        platform: str = "web",
        manual: bool = False,
    ) -> HealRoundResult:
        """对 case_id 执行一次 HealRound（claim → candidates → Finalization）。

        - 无活跃代码 / 无 failed step → 不触发 Heal（保留原 CaseResult 与 terminal_reason）。
        - 同 execution+case 已存在 HealRecord → claim 拒绝（Manual 亦同）。
        """
        # 1) 前置（只读）：冻结活跃代码（禁止 latest 后门）
        from app.services.execution_code_resolver import ExecutionCodeResolver
        active_code = ExecutionCodeResolver(self._db).get_active_code(execution_id, case_id)
        if active_code is None:
            return HealRoundResult(
                retry_status="skipped", rounds_consumed=False,
                error_message="无冻结活跃代码，不触发 Heal",
            )

        # 2) root failed step = step_index 最小者；其余 failed step 作上下文
        failed_steps = (
            self._db.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == execution_id,
                ExecutionStep.case_id == case_id,
                ExecutionStep.status == "failed",
            )
            .order_by(ExecutionStep.step_index)
            .all()
        )
        if not failed_steps:
            return HealRoundResult(
                retry_status="skipped", rounds_consumed=False,
                error_message="无失败步骤，不触发 Heal（保留原 CaseResult 与 terminal_reason）",
            )
        root_step = failed_steps[0]

        # 3) claim（per-execution 串行化 + stop 检查 + UNIQUE 竞态）
        outcome = self.claim(execution_id, case_id, root_step.id, active_code.id)
        if not outcome.ok:
            return HealRoundResult(
                retry_status="failed", rounds_consumed=False,
                error_message=f"HealRound claim 拒绝: {outcome.reason}",
            )
        record = outcome.record

        # 4) 运行 Round（candidate 生成 → Validator → 整 Case rerun → Finalization）
        return await self._run_round(
            record, execution_id, case_id, project_id, root_step,
            active_code, page, platform,
        )

    # ═══════════════════════════════════════════════
    # claim — DB 级竞态（UNIQUE + 串行化临界区）
    # ═══════════════════════════════════════════════

    def claim(
        self,
        execution_id: int,
        case_id: int,
        root_step_id: int,
        original_code_id: int,
    ) -> ClaimOutcome:
        """串行化 claim 一个 HealRound（round_no=1）。

        同一临界区内：
          ① SELECT ... FOR UPDATE 锁 executions 行（MySQL；SQLite 由 per-exec lock 串行化）
          ② 先查 stop_requested_at（DB 行状态/end_time 为权威 + 进程内 flag 为 fast-path）→ 拒绝
          ③ 已存在 HealRecord（同 execution+case）→ 拒绝（spec 7，Manual 亦同）
          ④ INSERT round_no=1 → IntegrityError → 拒绝（并发 claim）
        """
        from app.models.execution import Execution

        with _get_exec_lock(execution_id):
            exec_row = (
                self._db.query(Execution)
                .filter(Execution.id == execution_id)
                .with_for_update()
                .first()
            )
            if self._stop_requested(execution_id, exec_row):
                return ClaimOutcome(ok=False, reason="execution stop_requested")

            # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 HealRecord 写入
            from app.services.execution_finalizer import TERMINAL_STATUSES
            if exec_row is not None and exec_row.status in TERMINAL_STATUSES:
                return ClaimOutcome(ok=False, reason="execution sealed（终态），禁止自愈")

            existing = (
                self._db.query(HealRecord)
                .filter(
                    HealRecord.execution_id == execution_id,
                    HealRecord.case_id == case_id,
                )
                .first()
            )
            if existing is not None:
                return ClaimOutcome(ok=False, reason="该用例已存在 HealRecord，禁止再次自愈")

            record = HealRecord(
                execution_id=execution_id,
                case_id=case_id,
                round_no=1,
                root_execution_step_id=root_step_id,
                execution_step_id=root_step_id,  # 兼容既有 heals 列表接口
                original_code_id=original_code_id,
                retry_status="pending",
                retry_count=0,
                attempts=json.dumps([], ensure_ascii=False),
            )
            self._db.add(record)
            try:
                self._db.flush()
                self._db.commit()
            except IntegrityError:
                self._db.rollback()
                return ClaimOutcome(ok=False, reason="并发 claim 冲突（UNIQUE 约束生效）")
            self._db.refresh(record)
            return ClaimOutcome(ok=True, record=record)

    # ═══════════════════════════════════════════════
    # Round 主体 — candidate 串行处理 + Winner 钉死
    # ═══════════════════════════════════════════════

    async def _run_round(
        self,
        record: HealRecord,
        execution_id: int,
        case_id: int,
        project_id: int,
        root_step: ExecutionStep,
        active_code,
        page,
        platform: str,
    ) -> HealRoundResult:
        """candidate 按 AI attempt 顺序串行处理；第一个『Validator 通过且整 Case
        rerun Resolver=success』的 candidate 立即成为 winning candidate 并进入
        Finalization，Round 立即停止后续 AI attempt。

        混合失败钉死：
          - ≥1 candidate 过 Validator 且被实际 rerun、但全部 rerun 失败 → heal_exhausted
          - 三 attempt 全部未过 Validator（无可 rerun candidate）→ validation_error
          - 任一 rerun 因 worker_failed/deadline_exceeded 等明确基础设施原因终止 →
            Round 按基础设施故障收口（error_type=对应基础设施错误，case 升级 execution_failed）
        """
        deadline = time.monotonic() + settings.AI_HEAL_BUDGET_SECONDS
        error_ctx = await self._heal_svc._capture_failure_context(root_step, page, platform=platform)
        original_code_text = active_code.code_content

        attempts: list[dict] = []
        winning: Optional[dict] = None
        infra_error: Optional[tuple[str, str]] = None   # (error_type, message)
        last_ai_error: Optional[str] = None
        any_valid_rerun = False

        for attempt_no in range(1, settings.MAX_HEAL_RETRY + 1):
            # 每次 AI attempt 开始前复查 stop（内存 flag 为 fast-path，DB 为权威）
            if self._stop_requested_ctx(execution_id):
                return self._close_round(record, "cancelled_by_recovery", None, attempts)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                attempts.append(self._mk_attempt(
                    attempt_no, None, validator_result=False,
                    rerun_result=None, rerun_error_type="deadline_exceeded",
                    error_message="heal_deadline_exceeded（attempt 开始前已过期）",
                ))
                infra_error = ("deadline_exceeded", "heal_deadline_exceeded")
                break

            prompt = self._heal_svc._build_heal_prompt(error_ctx, original_code_text, root_step, platform=platform)

            # AI 调用（quota/slot/HTTP/Retry-After/backoff 全受 heal deadline 约束）
            ai_ok, raw_code, ai_err = await self._ai_attempt(prompt, platform, remaining)
            if not ai_ok:
                last_ai_error = ai_err
                attempts.append(self._mk_attempt(
                    attempt_no, None, validator_result=False,
                    rerun_result=None, rerun_error_type=ai_err,
                    error_message=f"AI 阶段失败: {ai_err}",
                ))
                if ai_err == "deadline_exceeded":
                    infra_error = ("deadline_exceeded", "heal_deadline_exceeded")
                    break
                continue

            candidate_code = self._heal_svc._extract_code(raw_code)

            # 每 candidate 先 CodeValidator；失败只写 attempts[]，不落 generated_codes
            validation_error = self._heal_svc._validate_healed(candidate_code, platform=platform)
            if validation_error:
                attempts.append(self._mk_attempt(
                    attempt_no, candidate_code, validator_result=False,
                    validator_error=validation_error[:500],
                    rerun_result=None, rerun_error_type=None,
                ))
                continue

            # candidate 过 Validator → 【整 Case rerun】（root failed step 仅用于定位
            # 修复目标与上下文，不是 rerun 范围；只有整 Case rerun Resolver=success 才允许 Finalization）
            rerun = await self._rerun_case(
                execution_id, case_id, candidate_code, page, platform, deadline
            )
            attempts.append(self._mk_attempt(
                attempt_no, candidate_code, validator_result=True,
                rerun_result="success" if rerun.ok else "failed",
                rerun_error_type=rerun.error_type,
                rerun_message=(rerun.error_message or "")[:500],
            ))
            if rerun.ok:
                winning = {
                    "attempt_no": attempt_no,
                    "candidate_code": candidate_code,
                    "prompt": prompt,
                    "step_results": rerun.step_results,
                }
                break
            any_valid_rerun = True
            if rerun.error_type in _INFRA_RERUN_ERRORS:
                infra_error = (rerun.error_type, rerun.error_message)
                break  # Rerun Failure Precedence：基础设施故障收口

        # ── 落盘 attempts（Round 终态前最后一次更新；Finalization 还会追加 winning 事实）──
        record.retry_count = len(attempts)
        record.attempts = json.dumps(attempts, ensure_ascii=False)
        self._db.commit()

        if winning is not None:
            return await self._finalize_win(
                record, execution_id, case_id, winning, root_step,
            )
        if infra_error is not None:
            return self._close_round(
                record, "failed", infra_error[0], attempts,
                upgrade_execution_failed=True, upgrade_why=infra_error[1],
            )
        if any_valid_rerun:
            # 混合失败钉死：至少一个 valid candidate 被实际 rerun 且全部失败（均非基础设施）→ heal_exhausted
            return self._close_round(record, "failed", "heal_exhausted", attempts)
        # 无可 rerun candidate：『AI 调用最终失败』一律记 ai_request_failed
        if last_ai_error == "ai_request_failed":
            return self._close_round(record, "failed", "ai_request_failed", attempts)
        if last_ai_error == "ai_schema_error":
            return self._close_round(record, "failed", "ai_schema_error", attempts)
        return self._close_round(record, "failed", "validation_error", attempts)

    # ═══════════════════════════════════════════════
    # Candidate rerun — 整 Case 重新执行（内存步骤证据）
    # ═══════════════════════════════════════════════

    async def _rerun_case(
        self,
        execution_id: int,
        case_id: int,
        candidate_code: str,
        page,
        platform: str,
        deadline: float,
    ) -> RerunResult:
        """用 candidate 代码跑完整 Case（整 Case rerun）；步骤结果只进内存（attempts[]）。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return RerunResult(ok=False, error_type="deadline_exceeded",
                               error_message="heal_deadline_exceeded")
        if platform == "android":
            return self._rerun_case_sync(
                execution_id, case_id, candidate_code, page, deadline
            )
        return await self._rerun_case_async(
            execution_id, case_id, candidate_code, page, deadline
        )

    async def _rerun_case_async(
        self, execution_id: int, case_id: int, candidate_code: str, page, deadline: float,
    ) -> RerunResult:
        from app.utils.code_injector import CodeInjector
        from app.utils.safe_playwright import SafePlaywright
        from app.services.playwright_service import _build_namespace

        try:
            code = CodeInjector.inject(candidate_code)
        except SecurityException:
            return RerunResult(ok=False, error_type="worker_failed", error_message="候选代码注入失败")

        hooks = _HealRunHooks(execution_id, case_id)
        safe = SafePlaywright(page)
        namespace = _build_namespace(safe, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            return RerunResult(ok=False, error_type="worker_failed",
                               error_message=f"候选代码编译失败: {str(e)[:200]}")

        run_test = namespace.get("run_test")
        if not run_test:
            return RerunResult(ok=False, error_type="worker_failed", error_message="候选代码缺少 run_test")

        try:
            remaining = max(0.1, deadline - time.monotonic())
            result = await asyncio.wait_for(run_test(safe), timeout=min(120.0, remaining))
        except asyncio.TimeoutError:
            if time.monotonic() >= deadline:
                return RerunResult(ok=False, error_type="deadline_exceeded",
                                   error_message="heal_deadline_exceeded（rerun 超时）")
            return RerunResult(ok=False, error_type="worker_failed", error_message="候选代码 rerun 超时")
        except Exception as e:
            return RerunResult(ok=False, error_type="worker_failed",
                               error_message=f"候选代码 rerun 异常: {str(e)[:200]}")

        success = result.get("success", False) if isinstance(result, dict) else False
        if success:
            return RerunResult(ok=True, error_type=None, step_results=dict(hooks.results))
        return self._classify_rerun_failure(hooks.results, result)

    def _rerun_case_sync(
        self, execution_id: int, case_id: int, candidate_code: str, driver, deadline: float,
    ) -> RerunResult:
        from app.utils.appium_code_injector import AppiumCodeInjector
        from app.services.appium_service import _build_sync_namespace

        try:
            code = AppiumCodeInjector.inject(candidate_code)
        except SecurityException:
            return RerunResult(ok=False, error_type="worker_failed", error_message="候选代码注入失败")

        hooks = _SyncHealRunHooks(execution_id, case_id)
        namespace = _build_sync_namespace(driver, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            return RerunResult(ok=False, error_type="worker_failed",
                               error_message=f"候选代码编译失败: {str(e)[:200]}")

        run_test = namespace.get("run_test")
        if not run_test:
            return RerunResult(ok=False, error_type="worker_failed", error_message="候选代码缺少 run_test")

        if time.monotonic() >= deadline:
            return RerunResult(ok=False, error_type="deadline_exceeded",
                               error_message="heal_deadline_exceeded（rerun 超时）")
        try:
            result = run_test(namespace["driver"])
        except Exception as e:
            return RerunResult(ok=False, error_type="worker_failed",
                               error_message=f"候选代码 rerun 异常: {str(e)[:200]}")

        success = result.get("success", False) if isinstance(result, dict) else False
        if success:
            return RerunResult(ok=True, error_type=None, step_results=dict(hooks.results))
        return self._classify_rerun_failure(hooks.results, result)

    @staticmethod
    def _classify_rerun_failure(step_results: dict, result) -> RerunResult:
        """整 Case rerun 失败分类（Rerun Failure Precedence）：
        内存步骤证据 → Resolver；business_assertion_failed → business_failure；
        其余（element/env 失败、无证据）→ case_rerun_failed（非基础设施 → heal_exhausted 桶）。
        """
        from app.utils.case_state_resolver import resolve

        msg = ""
        if isinstance(result, dict):
            msg = str(result.get("message", ""))[:300]
        step_dicts = [step_results[k] for k in sorted(step_results)]
        if not step_dicts:
            return RerunResult(ok=False, error_type="case_rerun_failed",
                               error_message=f"候选 rerun 无步骤证据: {msg or 'success=False'}")
        status, reason = resolve(step_dicts, {"reason": "business_failure"})
        if reason == "business_failure":
            return RerunResult(ok=False, error_type="business_failure",
                               error_message=msg or "业务断言失败")
        return RerunResult(ok=False, error_type="case_rerun_failed",
                           error_message=msg or f"rerun 失败（resolver={reason}）")

    # ═══════════════════════════════════════════════
    # Finalization — 两阶段（Phase A / Phase B）
    # ═══════════════════════════════════════════════

    async def _finalize_win(
        self,
        record: HealRecord,
        execution_id: int,
        case_id: int,
        winning: dict,
        root_step: ExecutionStep,
    ) -> HealRoundResult:
        """Phase A【成功事实落盘】→ Phase B【Finalization Transaction】。"""
        # Phase A：独立小事务（成功即不可回滚）—— rerun_result=success + candidate_code → finalizing
        if not self._phase_a_persist(record, winning):
            return self._close_round(
                record, "failed", "heal_finalization_error", json.loads(record.attempts or "[]"),
            )

        # Phase B：纳入 Execution-level 线性化边界；进入临界区后【重新读取最新】stop_requested_at
        steps_hash = self._manifest_steps_hash(execution_id, case_id)
        last_err = ""
        for attempt in range(1, _PHASE_B_MAX_RETRY + 1):
            outcome = self._phase_b_finalize(
                record, execution_id, case_id, winning, steps_hash,
            )
            if outcome == "success":
                return HealRoundResult(
                    heal_id=record.id, retry_status="success",
                    healed_code_id=record.healed_code_id, healed_code=record.healed_code or winning["candidate_code"],
                )
            if outcome == "cancelled":
                return HealRoundResult(
                    heal_id=record.id, retry_status="cancelled_by_recovery",
                    error_message="Phase B 检出 stop_requested，放弃 success Finalization",
                )
            # retryable：保持 finalizing，同 Round 重试（不重调 AI、不重 rerun）
            last_err = outcome
        # 不可恢复：先 HealRecord finalizing→failed(heal_finalization_error) 使 Round terminal，再 Execution.failed
        return self._close_round(
            record, "failed", "heal_finalization_error",
            json.loads(record.attempts or "[]"),
            upgrade_why=f"Phase B 重试仍失败: {last_err}",
        )

    def _phase_a_persist(self, record: HealRecord, winning: dict) -> bool:
        """Phase A 独立小事务：attempts[].rerun_result=success + candidate_code → finalizing → COMMIT。"""
        try:
            record = self._db.query(HealRecord).filter(HealRecord.id == record.id).first()
            if record is None:
                return False
            attempts = json.loads(record.attempts) if record.attempts else []
            for entry in attempts:
                if entry.get("attempt") == winning["attempt_no"]:
                    entry["rerun_result"] = "success"
                    entry["candidate_code"] = winning["candidate_code"]
            record.attempts = json.dumps(attempts, ensure_ascii=False)
            record.healed_code = winning["candidate_code"][:5000]
            record.heal_prompt = winning["prompt"][:5000]
            record.retry_status = "finalizing"
            self._db.commit()
            return True
        except Exception:
            logger.exception("Phase A 落盘失败: heal_id=%s", record.id)
            self._db.rollback()
            return False

    def _phase_b_finalize(
        self,
        record: HealRecord,
        execution_id: int,
        case_id: int,
        winning: dict,
        steps_hash: Optional[str],
    ) -> str:
        """Phase B Finalization Transaction（返回 success / cancelled / retryable 错误信息 / 不可恢复标记）。

        进入 per-execution 临界区（与 Stop/Recovery/Finalizer 同一锁）后【重新读取最新】
        stop_requested_at——NULL 才允许成功 Finalization；非 NULL 则禁止 Finalize success、
        不切换 active_code_id，Round 收口 cancelled_by_recovery（由 ExecutionFinalizer 走 stopped 路径）。
        事务内容缺一不可：GeneratedCode + HealRecord.healed_code_id + retry_status=success +
        runtime_state.active_code_id + runtime_state.case_status=success +
        runtime_state.terminal_reason=normal_success + Step 状态 → COMMIT。
        """
        from app.models.execution import Execution

        with _get_exec_lock(execution_id):
            exec_row = (
                self._db.query(Execution)
                .filter(Execution.id == execution_id)
                .with_for_update()
                .first()
            )
            # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 HealRecord/Runtime/Step 写入。
            # 正常 Heal 发生在 running/healing 中不会触发；仅异常时序（Recovery 已先 Seal）
            # 时抛 SealedExecutionError，由上层捕获，不产生任何后续写。
            from app.utils.seal_guard import guard_not_sealed
            guard_not_sealed(self._db, execution_id)
            if self._stop_requested(execution_id, exec_row):
                # 禁止用 Phase A 时缓存的 stop_requested_at：此处为最新读取
                record.retry_status = "cancelled_by_recovery"
                record.error_type = None
                self._db.commit()
                return "cancelled"

            try:
                gen = GeneratedCode(
                    case_id=case_id,
                    code_content=winning["candidate_code"],
                    code_language="python",
                    generation_prompt=winning["prompt"],
                    ai_model=settings.OPENAI_MODEL,
                    is_valid=1,
                    is_healed=1,
                    source_steps_hash=steps_hash,  # 禁止 Execution 级单数字段：用 per-case steps_hash
                )
                self._db.add(gen)
                self._db.flush()

                record.healed_code_id = gen.id
                record.retry_status = "success"
                record.error_type = None

                # runtime_state（唯一持久化真源）：active_code_id 切换 + case 终态
                runtime_state = {}
                if exec_row and exec_row.runtime_state_json:
                    try:
                        runtime_state = json.loads(exec_row.runtime_state_json)
                    except (TypeError, ValueError):
                        runtime_state = {}
                runtime_state[str(case_id)] = {
                    "active_code_id": gen.id,
                    "case_status": "success",
                    "terminal_reason": "normal_success",
                }
                exec_row.runtime_state_json = json.dumps(runtime_state, ensure_ascii=False)

                # canonical Step 状态 → success（仅 winning candidate 允许改写原始失败事实）
                steps = (
                    self._db.query(ExecutionStep)
                    .filter(
                        ExecutionStep.execution_id == execution_id,
                        ExecutionStep.case_id == case_id,
                    )
                    .all()
                )
                for s in steps:
                    s.status = "success"
                    s.error_type = None
                    s.skip_reason = None
                    s.error_message = None
                    s.exception_type = None

                self._db.commit()
                logger.info(
                    "Heal Finalization 成功: execution_id=%s case=%s heal_id=%s code_id=%s",
                    execution_id, case_id, record.id, gen.id,
                )
                return "success"
            except Exception as e:
                self._db.rollback()
                # Failure Classification（钉死，禁止按第 N 次失败猜测）：
                # IntegrityError / ProgrammingError（Schema/FK/UNIQUE/代码事实完整性）→ 不可恢复
                # OperationalError（瞬时事务/基础设施：deadlock/lock/连接）→ retryable
                from sqlalchemy.exc import IntegrityError as _IE, ProgrammingError as _PE, OperationalError as _OE
                if isinstance(e, (_IE, _PE)) or isinstance(getattr(e, "orig", None), (_IE, _PE)):
                    logger.error("Phase B 不可恢复失败: heal_id=%s %s", record.id, e)
                    return f"unrecoverable: {str(e)[:200]}"
                if isinstance(e, _OE) or isinstance(getattr(e, "orig", None), (_OE,)) \
                        or "database is locked" in str(e) or "deadlock" in str(e).lower():
                    logger.warning("Phase B 瞬时失败（可重试）: heal_id=%s %s", record.id, e)
                    return f"retryable: {str(e)[:200]}"
                logger.error("Phase B 未分类失败（保守不可恢复）: heal_id=%s %s", record.id, e)
                return f"unrecoverable: {str(e)[:200]}"

    def retry_finalization(self, execution_id: int, case_id: int) -> HealRoundResult:
        """对 stuck finalizing 的 Round 重试 Phase B（不重调 AI、不重 rerun）。

        winning candidate 从已持久化的 attempts[]（rerun_result=success）定位。
        """
        record = (
            self._db.query(HealRecord)
            .filter(
                HealRecord.execution_id == execution_id,
                HealRecord.case_id == case_id,
                HealRecord.retry_status == "finalizing",
            )
            .first()
        )
        if record is None:
            return HealRoundResult(retry_status="failed", error_message="无 finalizing 状态的 HealRecord")
        winning = self._winning_from_attempts(record)
        if winning is None:
            return HealRoundResult(retry_status="failed", error_message="finalizing 记录缺少 winning candidate")
        steps_hash = self._manifest_steps_hash(execution_id, case_id)
        outcome = self._phase_b_finalize(record, execution_id, case_id, winning, steps_hash)
        if outcome == "success":
            return HealRoundResult(
                heal_id=record.id, retry_status="success",
                healed_code_id=record.healed_code_id, healed_code=record.healed_code or winning["candidate_code"],
            )
        if outcome == "cancelled":
            return HealRoundResult(
                heal_id=record.id, retry_status="cancelled_by_recovery",
                error_message="Phase B 检出 stop_requested，放弃 success Finalization",
            )
        return self._close_round(
            record, "failed", "heal_finalization_error",
            json.loads(record.attempts or "[]"),
            upgrade_why=f"Phase B 重试仍失败: {outcome[:200]}",
        )

    # ═══════════════════════════════════════════════
    # Round 收口（终态落盘）
    # ═══════════════════════════════════════════════

    def _close_round(
        self,
        record: HealRecord,
        retry_status: str,
        error_type: Optional[str],
        attempts: list[dict],
        *,
        upgrade_execution_failed: bool = False,
        upgrade_why: str = "",
    ) -> HealRoundResult:
        """Round 终态落盘（failed / cancelled_by_recovery）。

        - error_type 仅 failed 必填（七值之一）；success 与 cancelled_by_recovery 时=NULL。
        - 真实基础设施故障（worker_failed/deadline_exceeded）→ 该 Case 升级 execution_failed：
          把基础设施事实写到 canonical failed step（真实故障证据，非候选结果改写）。
        - 其余失败（heal_exhausted/validation_error/ai_*）→ canonical Step 保持原始失败原样，
          保留原 CaseResult 与 terminal_reason（business_failure 仍是 business_failure）。
        """
        record.retry_status = retry_status
        record.error_type = error_type
        record.attempts = json.dumps(attempts, ensure_ascii=False) if attempts else record.attempts
        if retry_status == "failed":
            record.retry_count = len(attempts)
        if upgrade_execution_failed and error_type in _INFRA_RERUN_ERRORS:
            try:
                from app.models.execution import Execution
                steps = (
                    self._db.query(ExecutionStep)
                    .filter(
                        ExecutionStep.execution_id == record.execution_id,
                        ExecutionStep.case_id == record.case_id,
                        ExecutionStep.status == "failed",
                    )
                    .all()
                )
                for s in steps:
                    s.error_type = error_type  # worker_failed/deadline_exceeded → Resolver 归 execution_failed
                logger.warning(
                    "Heal 基础设施故障，Case 升级 execution_failed: execution=%s case=%s %s",
                    record.execution_id, record.case_id, upgrade_why,
                )
            except Exception:
                logger.exception("Heal 基础设施故障升级失败: heal_id=%s", record.id)
        self._db.commit()
        return HealRoundResult(
            heal_id=record.id,
            retry_status=retry_status,
            error_type=error_type,
            error_message=upgrade_why or "",
            upgrade_execution_failed=upgrade_execution_failed,
        )

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _stop_requested_ctx(self, execution_id: int) -> bool:
        """DB 权威 + 内存 fast-path 的 stop 复查（每次 AI attempt 前调用）"""
        from app.models.execution import Execution
        exec_row = (
            self._db.query(Execution)
            .filter(Execution.id == execution_id)
            .first()
        )
        return self._stop_requested(execution_id, exec_row)

    @staticmethod
    def _stop_requested(execution_id: int, exec_row) -> bool:
        """stop_requested 判定：DB stop_requested_at 列是唯一权威（P0-9），
        终态/end_time 兼容存量数据兜底，内存 flag 仅 fast-path。"""
        from app.services.execution_state import is_stopped
        from app.services.execution_finalizer import TERMINAL_STATUSES
        if exec_row is not None:
            if exec_row.stop_requested_at is not None:
                return True
            if exec_row.status in TERMINAL_STATUSES or exec_row.end_time is not None:
                return True
        return is_stopped(execution_id)

    @staticmethod
    def _mk_attempt(
        attempt_no: int,
        candidate_code: Optional[str],
        validator_result: bool,
        rerun_result: Optional[str],
        rerun_error_type: Optional[str],
        validator_error: str = "",
        rerun_message: str = "",
        error_message: str = "",
    ) -> dict:
        """attempt 条目（必须含 candidate_code/validator_result/rerun_result/rerun_error_type）"""
        return {
            "attempt": attempt_no,
            "candidate_code": candidate_code[:5000] if candidate_code else None,
            "validator_result": validator_result,
            "validator_error": validator_error,
            "rerun_result": rerun_result,
            "rerun_error_type": rerun_error_type,
            "rerun_message": rerun_message,
            "error_message": error_message,
            "created_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _winning_from_attempts(record: HealRecord) -> Optional[dict]:
        """从已持久化 attempts[] 定位 winning candidate（rerun_result=success），绝不重调 AI"""
        try:
            attempts = json.loads(record.attempts) if record.attempts else []
        except (json.JSONDecodeError, TypeError):
            return None
        for entry in attempts:
            if entry.get("rerun_result") == "success" and entry.get("candidate_code"):
                return {
                    "attempt_no": entry.get("attempt"),
                    "candidate_code": entry["candidate_code"],
                    "prompt": "",
                }
        return None

    @staticmethod
    def _manifest_steps_hash(execution_id: int, case_id: int) -> Optional[str]:
        """Manifest.cases[当前 case_id].steps_hash（per-case，禁止 Execution 级单数字段）"""
        from app.models.execution import Execution
        db = None
        try:
            from app.db.database import SessionLocal
            db = SessionLocal()
            row = db.query(Execution).filter(Execution.id == execution_id).first()
            if row is None or not row.manifest_json:
                return None
            manifest = json.loads(row.manifest_json)
            for c in manifest.get("cases", []):
                if c.get("case_id") == case_id:
                    return c.get("steps_hash")
            return None
        except Exception:
            logger.exception("读取 Manifest steps_hash 失败: execution=%s case=%s", execution_id, case_id)
            return None
        finally:
            if db is not None:
                db.close()

    async def _ai_attempt(
        self, prompt: str, platform: str, remaining: float,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Heal AI 调用（受全局限流器 + heal deadline 约束）。

        返回 (ok, code, error_type)；error_type ∈ {ai_request_failed, ai_schema_error, deadline_exceeded}。
        『AI 调用最终失败』（429/5xx/连接重试耗尽/限流）一律记 ai_request_failed。
        """
        start = time.monotonic()
        try:
            if not settings.OPENAI_API_KEY:
                # Mock 模式不消耗限流额度
                code = self._heal_svc._call_heal_ai(prompt, platform=platform)
            else:
                if remaining <= 0:
                    return (False, None, "deadline_exceeded")
                if not ai_rate_limiter.acquire():
                    return (False, None, "ai_request_failed")
                if not ai_rate_limiter.acquire_slot(timeout=remaining):
                    ai_rate_limiter._rollback_quota()
                    return (False, None, "ai_request_failed")
                try:
                    code = await asyncio.to_thread(
                        self._heal_svc._call_heal_ai, prompt, platform=platform
                    )
                finally:
                    ai_rate_limiter.release_slot()
        except Exception as e:
            logger.error("Heal AI 调用失败: %s", e)
            return (False, None, "ai_request_failed")

        if time.monotonic() - start >= remaining:
            # 在途请求超 deadline → 本次结果标记 deadline_exceeded（不消费 candidate）
            return (False, None, "deadline_exceeded")

        if not code or "UNABLE_TO_HEAL" in code:
            return (False, None, "ai_schema_error")
        return (True, code, None)


# ═══════════════════════════════════════════════
# 候选整 Case rerun 的【内存】监控钩子
# （与执行期 _MonitorHooks 同接口，结果只进 attempts[]，绝不写 canonical ExecutionStep）
# ═══════════════════════════════════════════════

class _HealRunHooks:
    """Web 候选代码整 Case rerun 的内存步骤证据累积器"""

    def __init__(self, execution_id: int, case_id: int) -> None:
        self._execution_id = execution_id
        self._case_id = case_id
        self.results: dict[int, dict] = {}
        self._step_times: dict[int, float] = {}
        self._step_actions: dict[int, str] = {}

    async def on_step_before(self, step_no: int, action: str, target: str, value: str) -> None:
        self._step_times[step_no] = time.time()
        self._step_actions[step_no] = action

    async def on_step_after(self, step_no: int, status: str, error_msg: str = "") -> None:
        start = self._step_times.get(step_no, time.time())
        duration_ms = int((time.time() - start) * 1000)
        if status == "passed":
            self.results[step_no] = {
                "status": "success", "error_type": None, "skip_reason": None,
                "exception_type": None, "duration_ms": duration_ms,
            }
            return
        from app.services.playwright_service import _MonitorHooks
        error_type, exception_type = _MonitorHooks._classify_web_error(
            self._step_actions.get(step_no, ""), error_msg or ""
        )
        self.results[step_no] = {
            "status": "failed", "error_type": error_type, "skip_reason": None,
            "exception_type": exception_type, "duration_ms": duration_ms,
        }


class _SyncHealRunHooks:
    """Android 候选代码整 Case rerun 的内存步骤证据累积器（同步）"""

    def __init__(self, execution_id: int, case_id: int) -> None:
        self._execution_id = execution_id
        self._case_id = case_id
        self.results: dict[int, dict] = {}
        self._step_times: dict[int, float] = {}
        self._step_exceptions: dict[int, str] = {}

    def on_step_before(self, step_no: int, action: str, target: str, value: str) -> None:
        self._step_times[step_no] = time.time()

    def on_step_after(self, step_no: int, status: str, error_msg: str = "", exception_type: str = "") -> None:
        start = self._step_times.get(step_no, time.time())
        duration_ms = int((time.time() - start) * 1000)
        if status == "passed":
            self.results[step_no] = {
                "status": "success", "error_type": None, "skip_reason": None,
                "exception_type": None, "duration_ms": duration_ms,
            }
            return
        from app.services.appium_service import _SyncMonitorHooks
        error_type = _SyncMonitorHooks._classify_appium_error(exception_type, error_msg or "")
        self.results[step_no] = {
            "status": "failed", "error_type": error_type, "skip_reason": None,
            "exception_type": (exception_type or "")[:100], "duration_ms": duration_ms,
        }


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
