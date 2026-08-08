"""Playwright 安全执行引擎 — 沙箱执行 + 步骤监控 + 截图 + 视频 + 自愈调度"""

import asyncio
import json
import logging
import os
import sys
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.utils.code_validator import CodeValidator
from app.utils.code_injector import CodeInjector
from app.exceptions import SecurityException

logger = logging.getLogger("autopilot.playwright")

# ── 受限命名空间白名单 ──
ALLOWED_BUILTINS = frozenset({
    "len", "str", "range", "int", "float", "bool",
    "list", "dict", "tuple", "set", "print", "isinstance",
    "type", "enumerate", "zip", "map", "filter", "sorted",
    "min", "max", "sum", "abs", "round", "any", "all",
    "True", "False", "None", "Exception", "ValueError", "TypeError",
    "__import__",  # import 语句必需
})

# ── 全局停止标志 ──
_stop_flags: dict[int, bool] = {}
_stop_lock = threading.Lock()


class PlaywrightService:
    """Playwright 安全执行引擎

    执行流程:
      1. 创建 BrowserContext（视口 1920x1080，可选视频录制）
      2. 逐条执行用例：
         a. 获取最新代码 → 安全校验 → AST 注入监控
         b. 构建受限命名空间（白名单 builtins）
         c. 在沙箱中异步执行 run_test(page)
         d. 监控钩子自动记录步骤状态/截图/耗时
      3. 全部执行完 → 更新 status='healing' → 后台自愈
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 编排器调用接口 — 创建执行记录
    # ═══════════════════════════════════════════════

    def create_execution(
        self,
        project_id: int,
        case_ids: list[int],
        mode: str = "headless",
        batch_name: str | None = None,
    ) -> int:
        """创建 Execution 记录 + 初始化 ExecutionStep 记录

        Returns:
            execution_id
        """
        from app.models.test_case import TestCase

        execution = Execution(
            project_id=project_id,
            batch_name=batch_name,
            total_cases=len(case_ids),
            execution_mode=mode,
            status="running",
            start_time=datetime.utcnow(),
        )
        self._db.add(execution)
        self._db.flush()

        for cid in case_ids:
            case = self._db.query(TestCase).filter(TestCase.id == cid).first()
            if not case or not case.steps:
                continue
            try:
                steps = json.loads(case.steps)
            except json.JSONDecodeError:
                continue
            for s in steps:
                self._db.add(ExecutionStep(
                    execution_id=execution.id,
                    case_id=cid,
                    step_index=s.get("step_number", 1),
                    action=s.get("action", ""),
                    target_selector=s.get("target", ""),
                    input_value=s.get("value", ""),
                    status="pending",
                ))

        self._db.commit()
        self._db.refresh(execution)
        return execution.id

    # ═══════════════════════════════════════════════
    # 主入口 — 在后台线程中运行
    # ═══════════════════════════════════════════════

    def execute(
        self,
        project_id: int,
        case_ids: list[int],
        execution_id: int,
        mode: str = "headless",
    ) -> None:
        """在后台线程中运行整个执行流程

        此方法由 ExecutionRouter 在独立线程中调用。
        """
        try:
            asyncio.run(self._execute_async(project_id, case_ids, execution_id, mode))
        except Exception:
            logger.exception("执行异常: execution_id=%s", execution_id)
            self._update_execution_status(execution_id, "failed")

    async def _execute_async(
        self,
        project_id: int,
        case_ids: list[int],
        execution_id: int,
        mode: str,
    ) -> None:
        """异步执行主循环"""
        from playwright.async_api import async_playwright

        headless = mode == "headless"
        video_dir = self._ensure_dir(f"{settings.VIDEO_DIR}/{execution_id}") if not headless else None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=headless)
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    record_video_dir=video_dir,
                    record_video_size={"width": 1920, "height": 1080},
                ) if video_dir else await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()
                page.set_default_timeout(settings.PLAYWRIGHT_TIMEOUT)

                passed = 0
                failed = 0

                for case_id in case_ids:
                    if self._is_stopped(execution_id):
                        logger.info("执行被手动停止: execution_id=%s", execution_id)
                        break

                    try:
                        success = await self._execute_case(
                            page, execution_id, case_id
                        )
                        if success:
                            passed += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                        logger.exception("用例执行异常: case_id=%s", case_id)

                # 关闭浏览器
                await context.close()
                await browser.close()

                # 更新执行统计
                self._update_execution(execution_id, passed, failed)

                # 如果有失败步骤，进入自愈阶段
                if failed > 0:
                    self._update_execution_status(execution_id, "healing")
                    self._start_healing(execution_id, case_ids)
                else:
                    self._update_execution_status(execution_id, "completed")

        except Exception:
            logger.exception("浏览器启动异常")
            self._update_execution_status(execution_id, "failed")

    # ═══════════════════════════════════════════════
    # 单条用例执行
    # ═══════════════════════════════════════════════

    async def _execute_case(
        self, page, execution_id: int, case_id: int
    ) -> bool:
        """执行单条用例，返回是否成功"""
        # 1. 获取最新代码
        gen_code = (
            self._db.query(GeneratedCode)
            .filter(GeneratedCode.case_id == case_id, GeneratedCode.is_valid == 1)
            .order_by(GeneratedCode.created_at.desc())
            .first()
        )
        if not gen_code:
            logger.warning("用例 %s 无有效代码，跳过", case_id)
            return False

        code = gen_code.code_content

        # 2. 安全校验
        error = CodeValidator.validate(code)
        if error:
            raise SecurityException(f"用例 {case_id} 代码校验失败: {error}")

        # 3. AST 注入监控钩子
        try:
            code = CodeInjector.inject(code)
        except SecurityException:
            raise  # 注入失败，拒绝执行

        # 4. 初始化步骤记录
        self._init_steps(execution_id, case_id)

        # 5. 构建沙箱 + 执行
        hooks = _MonitorHooks(self._db, execution_id, case_id, page)
        namespace = _build_namespace(page, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            logger.error("代码编译失败: case_id=%s, %s", case_id, e)
            return False

        run_test = namespace.get("run_test")
        if not run_test:
            logger.error("代码缺少 run_test 函数: case_id=%s", case_id)
            return False

        try:
            result = await asyncio.wait_for(
                run_test(page), timeout=120.0
            )
            success = result.get("success", False) if isinstance(result, dict) else False
            return success
        except asyncio.TimeoutError:
            logger.error("用例执行超时(120s): case_id=%s", case_id)
            return False
        except Exception:
            logger.exception("用例执行异常: case_id=%s", case_id)
            return False

    # ═══════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════

    def _init_steps(self, execution_id: int, case_id: int) -> None:
        """初始化执行步骤记录（清空旧数据，创建 pending 记录）"""
        self._db.query(ExecutionStep).filter(
            ExecutionStep.execution_id == execution_id,
            ExecutionStep.case_id == case_id,
        ).delete()

        from app.models.test_case import TestCase
        case = self._db.query(TestCase).filter(TestCase.id == case_id).first()
        if not case or not case.steps:
            return

        try:
            steps = json.loads(case.steps)
        except json.JSONDecodeError:
            return

        for s in steps:
            self._db.add(ExecutionStep(
                execution_id=execution_id,
                case_id=case_id,
                step_index=s.get("step_number", 1),
                action=s.get("action", ""),
                target_selector=s.get("target", ""),
                input_value=s.get("value", ""),
                status="pending",
            ))
        self._db.commit()

    def _update_execution(self, execution_id: int, passed: int, failed: int) -> None:
        """更新执行记录"""
        exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
        if exec_row:
            exec_row.passed_cases = passed
            exec_row.failed_cases = failed
            self._db.commit()

    def _update_execution_status(self, execution_id: int, status: str) -> None:
        """更新执行状态"""
        exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
        if exec_row:
            exec_row.status = status
            if status in ("completed", "failed", "stopped"):
                exec_row.end_time = datetime.utcnow()
            self._db.commit()

    def _is_stopped(self, execution_id: int) -> bool:
        with _stop_lock:
            return _stop_flags.get(execution_id, False)

    @staticmethod
    def _ensure_dir(path: str) -> str:
        Path(path).mkdir(parents=True, exist_ok=True)
        return path

    def _start_healing(self, execution_id: int, case_ids: list[int]) -> None:
        """启动后台自愈线程 — 重新启动浏览器，逐个修复失败步骤"""
        def _heal():
            from app.db.database import SessionLocal
            from app.services.heal_service import HealService
            from app.models.execution import Execution
            from app.models.project import Project

            db = SessionLocal()
            try:
                logger.info("开始自愈: execution_id=%s", execution_id)
                failed_steps = (
                    db.query(ExecutionStep)
                    .filter(
                        ExecutionStep.execution_id == execution_id,
                        ExecutionStep.status == "failed",
                    )
                    .all()
                )
                if not failed_steps:
                    logger.info("无失败步骤，跳过自愈")
                    exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                    if exec_row:
                        exec_row.status = "completed"
                        exec_row.end_time = datetime.utcnow()
                        db.commit()
                    return

                # 获取项目 ID
                exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                project_id = exec_row.project_id if exec_row else None
                if not project_id:
                    logger.error("无法获取项目 ID: execution_id=%s", execution_id)
                    return

                # 获取目标 URL
                project = db.query(Project).filter(Project.id == project_id).first()
                target_url = project.target_url if project else "https://example.com"

                # 启动浏览器
                async def _heal_async():
                    from playwright.async_api import async_playwright

                    heal_service = HealService(db)
                    healed = 0
                    still_failed = 0

                    async with async_playwright() as pw:
                        browser = await pw.chromium.launch(headless=True)
                        context = await browser.new_context(
                            viewport={"width": 1920, "height": 1080},
                        )
                        page = await context.new_page()
                        page.set_default_timeout(settings.PLAYWRIGHT_TIMEOUT)

                        try:
                            await page.goto(target_url, wait_until="networkidle")
                        except Exception as e:
                            logger.warning("自愈导航失败: %s，继续尝试修复", e)

                        for step in failed_steps:
                            if self._is_stopped(execution_id):
                                logger.info("自愈被手动停止: execution_id=%s", execution_id)
                                break

                            try:
                                success = await heal_service.try_heal(
                                    execution_id=execution_id,
                                    step=step,
                                    page=page,
                                    project_id=project_id,
                                    max_retries=settings.MAX_HEAL_RETRY,
                                )
                                if success:
                                    healed += 1
                                else:
                                    still_failed += 1
                            except Exception:
                                logger.exception("自愈异常: step_id=%s", step.id)
                                still_failed += 1

                        await context.close()
                        await browser.close()

                    # 更新执行统计
                    exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                    if exec_row:
                        # passed_cases += healed 数量，failed_cases 减去已修复的
                        exec_row.passed_cases = (exec_row.passed_cases or 0) + healed
                        exec_row.failed_cases = still_failed
                        exec_row.status = "completed"
                        exec_row.end_time = datetime.utcnow()
                        db.commit()

                    logger.info(
                        "自愈完成: execution_id=%s healed=%s still_failed=%s",
                        execution_id, healed, still_failed,
                    )

                asyncio.run(_heal_async())

            except Exception:
                logger.exception("自愈过程异常")
                try:
                    exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                    if exec_row:
                        exec_row.status = "failed"
                        exec_row.end_time = datetime.utcnow()
                        db.commit()
                except Exception:
                    pass
            finally:
                db.close()

        t = threading.Thread(target=_heal, daemon=True)
        t.start()


# ═══════════════════════════════════════════════
# 监控钩子
# ═══════════════════════════════════════════════

class _MonitorHooks:
    """注入到用户代码中的 __monitor_before / __monitor_after 钩子

    用法（由 CodeInjector 自动注入）:
        __monitor_before(1, "fill", "#username", "admin")
        try:
            await page.locator("#username").fill("admin")
        except Exception as __ae:
            __monitor_after(1, "failed", str(__ae))
            raise
        else:
            __monitor_after(1, "passed", "")
    """

    def __init__(
        self, db: Session, execution_id: int, case_id: int, page
    ) -> None:
        self._db = db
        self._execution_id = execution_id
        self._case_id = case_id
        self._page = page
        self._step_times: dict[int, float] = {}
        self._screenshot_dir = f"uploads/screenshots/{execution_id}/{case_id}"
        Path(self._screenshot_dir).mkdir(parents=True, exist_ok=True)

    async def on_step_before(self, step_no: int, action: str, target: str, value: str) -> None:
        """步骤执行前：记录时间 + 截图 before + 创建/更新 DB 记录"""
        self._step_times[step_no] = time.time()

        # 截图 before
        screenshot_before = ""
        try:
            path = f"{self._screenshot_dir}/step_{step_no}_before.jpg"
            await self._page.screenshot(path=path, type="jpeg", quality=80, full_page=False)
            screenshot_before = path
        except Exception as e:
            logger.warning("截图 before 失败: %s", e)

        # 更新 DB
        self._upsert_step(step_no, {
            "action": action,
            "target_selector": target,
            "input_value": value,
            "screenshot_before": screenshot_before,
            "status": "running",
        })

    async def on_step_after(self, step_no: int, status: str, error_msg: str = "") -> None:
        """步骤执行后：计算耗时 + 截图 after + 更新 DB 记录"""
        start = self._step_times.get(step_no, time.time())
        duration_ms = int((time.time() - start) * 1000)

        # 截图 after
        screenshot_after = ""
        try:
            path = f"{self._screenshot_dir}/step_{step_no}_after.jpg"
            await self._page.screenshot(path=path, type="jpeg", quality=80, full_page=False)
            screenshot_after = path
        except Exception as e:
            logger.warning("截图 after 失败: %s", e)

        # 更新 DB
        update_data = {
            "status": "success" if status == "passed" else "failed",
            "screenshot_after": screenshot_after,
            "duration_ms": duration_ms,
        }
        if status == "failed" and error_msg:
            update_data["error_message"] = error_msg[:500]
            update_data["log_output"] = f"[FAIL] step {step_no}: {error_msg[:500]}"
        else:
            update_data["log_output"] = f"[PASS] step {step_no}: {duration_ms}ms"

        self._upsert_step(step_no, update_data)

    def _upsert_step(self, step_no: int, data: dict) -> None:
        """创建或更新执行步骤记录"""
        try:
            step = (
                self._db.query(ExecutionStep)
                .filter(
                    ExecutionStep.execution_id == self._execution_id,
                    ExecutionStep.case_id == self._case_id,
                    ExecutionStep.step_index == step_no,
                )
                .first()
            )
            if step:
                for key, val in data.items():
                    setattr(step, key, val)
            else:
                step = ExecutionStep(
                    execution_id=self._execution_id,
                    case_id=self._case_id,
                    step_index=step_no,
                    **data,
                )
                self._db.add(step)
            self._db.commit()
        except Exception as e:
            logger.error("更新步骤记录失败: step=%s, %s", step_no, e)
            try:
                self._db.rollback()
            except Exception:
                pass


# ═══════════════════════════════════════════════
# 沙箱命名空间构建
# ═══════════════════════════════════════════════

def _build_namespace(page, hooks: _MonitorHooks) -> dict:
    """构建受限执行命名空间

    仅注入白名单中的内置函数 + Playwright 必要模块。
    """
    import builtins as _builtins_module

    # 受限 builtins
    safe_builtins = {}
    for name in ALLOWED_BUILTINS:
        obj = getattr(_builtins_module, name, None)
        if obj is not None:
            safe_builtins[name] = obj

    # 注入自定义异常
    safe_builtins["Exception"] = Exception
    safe_builtins["ValueError"] = ValueError
    safe_builtins["TypeError"] = TypeError

    return {
        "__builtins__": safe_builtins,
        "page": page,
        "json": json,
        "time": time,
        "asyncio": asyncio,
        "datetime": datetime,
        "__monitor_before": hooks.on_step_before,
        "__monitor_after": hooks.on_step_after,
        "print": lambda *a, **kw: logger.info(" ".join(str(x) for x in a)),
    }


# ═══════════════════════════════════════════════
# 停止控制
# ═══════════════════════════════════════════════

def set_stop_flag(execution_id: int) -> None:
    """设置停止标志"""
    with _stop_lock:
        _stop_flags[execution_id] = True


def clear_stop_flag(execution_id: int) -> None:
    """清除停止标志"""
    with _stop_lock:
        _stop_flags.pop(execution_id, None)
