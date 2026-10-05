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
from app.utils.safe_playwright import SafePlaywright
from app.exceptions import SecurityException
from app.services.execution_finalizer import ExecutionFinalizer
from app.utils import terminal_reason as _tr

logger = logging.getLogger("autopilot.playwright")

# ── 受限命名空间白名单 ──
ALLOWED_BUILTINS = frozenset({
    "len", "str", "range", "int", "float", "bool",
    "list", "dict", "tuple", "set", "print", "isinstance",
    "enumerate", "zip", "map", "filter", "sorted",
    "min", "max", "sum", "abs", "round", "any", "all",
    "True", "False", "None", "Exception", "ValueError", "TypeError",
})

from app.services.execution_state import set_stop_flag, clear_stop_flag, is_stopped, db_stop_requested, generate_worker_id


# ── P1-1：Manifest 环境快照读取 + 浏览器 launcher 解析（三处 launch 的唯一取值来源）──

_BROWSER_LAUNCHERS = ("chromium", "firefox", "webkit")


def _load_manifest(exec_row) -> dict:
    """从 Execution 行解析 Manifest（缺省/损坏返回空 dict）。"""
    if exec_row is None or not getattr(exec_row, "manifest_json", None):
        return {}
    try:
        return json.loads(exec_row.manifest_json)
    except (TypeError, ValueError):
        return {}


def _resolve_launcher(pw, browser_type: str):
    """按 Manifest 冻结的 browser_type 解析 Playwright launcher。

    禁止 pw.chromium.launch() 作为默认旁路：browser_type 非法时显式抛错，
    不允许静默回退 chromium（会绕过 Manifest 冻结值）。
    """
    launcher = {
        "chromium": pw.chromium,
        "firefox": pw.firefox,
        "webkit": pw.webkit,
    }.get(browser_type)
    if launcher is None:
        raise SecurityException(f"Manifest 冻结的 browser_type 非法: {browser_type}")
    return launcher


def _env_from_manifest(manifest: dict) -> dict:
    """从 Manifest 提取执行期环境（URL/browser/mode/SSRF 快照）。

    旧 Execution（P1-1 前物化）缺少顶层键时回退 Manifest.project 快照
    （同为 Admission 冻结，非 Project 当前值）。
    """
    project_snap = manifest.get("project") or {}
    target_url = manifest.get("target_url") or project_snap.get("target_url") or ""
    test_path = manifest.get("test_path") or project_snap.get("test_path") or "/"
    browser_type = manifest.get("browser_type") or "chromium"
    execution_mode = manifest.get("execution_mode") or "headless"
    ssrf = manifest.get("ssrf_policy") or {}
    return {
        "target_url": target_url,
        "test_path": test_path,
        "browser_type": browser_type,
        "execution_mode": execution_mode,
        "headless": execution_mode != "headed",
        "allowed_hosts": list(ssrf.get("allowed_hosts") or []),
        "allowed_ports": list(ssrf.get("allowed_ports") or []),
    }


class PlaywrightService:
    """Playwright 安全执行引擎

    执行流程:
      1. 创建 BrowserContext（视口 1920x1080，可选视频录制）
      2. 逐条执行用例：
         a. 获取最新代码 → 安全校验 → AST 注入监控
         b. 构建受限命名空间（白名单 builtins）
         c. 在受限命名空间中异步执行 run_test(safe)（SafePlaywright 受控 API）
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
            status="queued",  # 排队中：后台线程真正启动后转 running
            start_time=datetime.utcnow(),
            heartbeat_at=datetime.utcnow(),
            worker_id=generate_worker_id(),
            progress=0,
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
            ExecutionFinalizer(self._db).seal(execution_id, "failed", _tr.EXECUTION_FAILED)

    async def _execute_async(
        self,
        project_id: int,
        case_ids: list[int],
        execution_id: int,
        mode: str,
    ) -> None:
        """异步执行主循环

        P1-1：mode 形参仅为兼容旧调用保留，执行阶段【忽略】——browser_type /
        execution_mode / target_url / ssrf_policy 一律只读 Manifest 冻结快照。
        """
        # 绕过 IDE 沙箱对 Playwright 子进程的拦截
        os.environ["TOOLHOST_SANDBOX_DISABLED"] = "true"
        from playwright.async_api import async_playwright

        # P1-1：执行期环境只读 Manifest 冻结快照（mode 形参已冻结于 Admission，
        # 执行阶段忽略，实际值只读 Manifest；禁止回读 Project 当前值）
        from app.models.execution import Execution as _Exec
        from app.utils.url_builder import build_target_url
        exec_row = self._db.query(_Exec).filter(_Exec.id == execution_id).first()
        env = _env_from_manifest(_load_manifest(exec_row))
        target_url = build_target_url(env["target_url"], env["test_path"])
        browser_type = env["browser_type"]
        headless = env["headless"]
        video_dir = self._ensure_dir(f"{settings.VIDEO_DIR}/{execution_id}") if not headless else None

        try:
            # P1-1：SSRF 策略只用 Manifest 快照 + 全局环境变量，禁止读 project.config_json
            from app.utils.url_policy import validate_target_url, UrlPolicy, install_network_policy
            error = validate_target_url(
                target_url,
                allowed_hosts=env["allowed_hosts"],
                allowed_ports=env["allowed_ports"],
            )
            if error:
                raise SecurityException(f"执行目标 URL 校验失败: {error}")
            policy = UrlPolicy(
                target_url,
                allowed_hosts=env["allowed_hosts"],
                allowed_ports=env["allowed_ports"],
            )

            # 原子状态机：queued → running（条件更新；影响行数 0 则放弃启动，
            # 可能已被 Stop 或 Recovery 抢先改写，杜绝 stopped→running 回跳）
            if not self._mark_running_if_queued(execution_id):
                logger.info("执行已被 Stop/Recovery 抢先改写，放弃启动: execution_id=%s", execution_id)
                return
            self._update_execution(execution_id)

            async with async_playwright() as pw:
                browser = await _resolve_launcher(pw, browser_type).launch(headless=headless)
                context_options: dict[str, Any] = {
                    "viewport": {"width": 1920, "height": 1080},
                    # 阻止 Service Worker：SW 控制的请求可绕过 route 拦截，形成盲区
                    "service_workers": "block",
                }
                if video_dir:
                    context_options["record_video_dir"] = video_dir
                    context_options["record_video_size"] = {"width": 1920, "height": 1080}
                context = await browser.new_context(**context_options)
                # BrowserContext 级网络拦截：HTTP/HTTPS/WebSocket + 重定向/iframe/popup/资源
                await install_network_policy(context, policy)
                page = await context.new_page()
                page.set_default_timeout(settings.PLAYWRIGHT_TIMEOUT)

                # 导航到项目目标 URL
                try:
                    await page.goto(target_url, wait_until="networkidle")
                    logger.info("已导航到目标 URL: %s", target_url)
                except Exception as e:
                    logger.warning("导航到 %s 失败: %s，继续执行", target_url, e)

                any_failure = False

                for case_id in case_ids:
                    if self._stop_requested(self._db, execution_id):
                        logger.info("执行被手动停止: execution_id=%s", execution_id)
                        break

                    try:
                        success = await self._execute_case(
                            page, execution_id, case_id
                        )
                        if not success:
                            any_failure = True
                    except Exception:
                        any_failure = True
                        logger.exception("用例执行异常: case_id=%s", case_id)

                    # 逐用例心跳保活（counters 为 Seal 时 Resolver 重算的 Derived Cache）
                    self._update_execution(execution_id)

                # 心跳保活（在关闭浏览器之前，避免异常丢失）
                self._update_execution(execution_id)

                # 关闭浏览器
                await context.close()
                await browser.close()

                # 终态收敛（统一经 ExecutionFinalizer）：先查 stop_requested（决策点
                # 必须查 DB stop_requested_at，内存 flag 为 fast-path）→ 为真则不进入
                # healing，剩余 pending→skipped(user_stopped)→stopped；为假才按
                # failed→healing / completed。
                if self._stop_requested(self._db, execution_id):
                    ExecutionFinalizer(self._db).seal_stopped(execution_id)
                elif any_failure:
                    self._update_execution_status(execution_id, "healing")
                    self._start_healing(execution_id, case_ids)
                else:
                    ExecutionFinalizer(self._db).seal(
                        execution_id, "completed", _tr.NORMAL_SUCCESS
                    )

        except Exception:
            logger.exception("浏览器启动异常")
            ExecutionFinalizer(self._db).seal(execution_id, "failed", _tr.EXECUTION_FAILED)

    # ═══════════════════════════════════════════════
    # 单条用例执行
    # ═══════════════════════════════════════════════

    async def _execute_case(
        self, page, execution_id: int, case_id: int
    ) -> bool:
        """执行单条用例，返回是否成功"""
        # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 Step/Runtime 写入
        from app.utils.seal_guard import guard_not_sealed
        guard_not_sealed(self._db, execution_id)

        # 1. 获取冻结的活跃代码（统一走 ExecutionCodeResolver，禁止 latest 后门）
        from app.services.execution_code_resolver import ExecutionCodeResolver
        gen_code = ExecutionCodeResolver(self._db).get_active_code(execution_id, case_id)
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

        # 4. 构建沙箱 + 执行（步骤已在 Admission 物化，禁止 delete+rebuild）
        hooks = _MonitorHooks(self._db, execution_id, case_id, page)
        safe = SafePlaywright(page)
        namespace = _build_namespace(safe, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            logger.error("代码编译失败: case_id=%s, %s", case_id, e)
            self._mark_case_failed(execution_id, case_id)
            return False

        run_test = namespace.get("run_test")
        if not run_test:
            logger.error("代码缺少 run_test 函数: case_id=%s", case_id)
            self._mark_case_failed(execution_id, case_id)
            return False

        try:
            result = await asyncio.wait_for(
                run_test(safe), timeout=120.0
            )
            success = result.get("success", False) if isinstance(result, dict) else False
            if not success:
                self._mark_case_failed(execution_id, case_id)
            return success
        except asyncio.TimeoutError:
            logger.error("用例执行超时(120s): case_id=%s", case_id)
            self._mark_case_failed(
                execution_id, case_id,
                error_type="execution_failed",
                error_message="case_execution_timeout(120s)",
            )
            return False
        except Exception:
            logger.exception("用例执行异常: case_id=%s", case_id)
            self._mark_case_failed(execution_id, case_id)
            return False

    def _mark_case_failed(self, execution_id: int, case_id: int, *,
                          error_type: str = "execution_failed",
                          error_message: str = "case_failed") -> None:
        """用例失败兜底：仅标记该 case 首个未终态步骤为 failed（禁止整 case 刷 failed）。

        真实步骤失败已由监控钩子按 error_type 分类记录；此处只兜底未终态步骤
        （如编译失败/超时/run_test 异常），避免覆盖已成功或已分类的步骤。
        """
        step = (
            self._db.query(ExecutionStep)
            .filter(
                ExecutionStep.execution_id == execution_id,
                ExecutionStep.case_id == case_id,
                ExecutionStep.status.in_(["pending", "running"]),
            )
            .order_by(ExecutionStep.step_index)
            .first()
        )
        if step is None:
            return
        step.status = "failed"
        step.error_type = error_type
        step.error_message = error_message
        self._db.commit()

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

    def _update_execution(self, execution_id: int, passed: int = 0, failed: int = 0) -> None:
        """执行过程心跳保活（仅写 heartbeat_at）。

        counters（passed_cases/failed_cases/progress）已改为 Seal 时从 Resolver
        重算的 Derived Cache，执行过程不再逐次自增维护；passed/failed 参数仅保留
        兼容调用方签名，不再写入。
        """
        try:
            exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
            if exec_row:
                exec_row.heartbeat_at = datetime.utcnow()
                self._db.commit()
        except Exception:
            logger.exception("更新执行心跳失败: execution_id=%s", execution_id)

    def _update_execution_status(self, execution_id: int, status: str) -> None:
        """更新执行状态"""
        try:
            exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
            if exec_row:
                exec_row.status = status
                if status in ("completed", "failed", "stopped", "interrupted"):
                    exec_row.end_time = datetime.utcnow()
                self._db.commit()
        except Exception:
            logger.exception("更新执行状态失败: execution_id=%s, status=%s", execution_id, status)

    def _is_stopped(self, execution_id: int) -> bool:
        return is_stopped(execution_id)

    @staticmethod
    def _stop_requested(db, execution_id: int) -> bool:
        """stop 判定：内存 flag 为 fast-path，决策点必须查 DB stop_requested_at（权威）。

        禁止只靠内存 flag 做决策（多实例下 flag 不跨进程；DB stop_requested_at 唯一权威）。
        """
        if is_stopped(execution_id):
            return True
        return db_stop_requested(db, execution_id)

    def _mark_running_if_queued(self, execution_id: int) -> bool:
        """原子状态机：queued → running（条件更新，线性化边界内执行）。

        仅当 status='queued' 时才更新为 running；影响行数为 0 说明已被 Stop
        （六步收口置 stopped）或 Recovery 抢先改写 → 放弃启动，杜绝 stopped→running 回跳。
        """
        from app.services.execution_state import get_execution_lock
        from sqlalchemy import update as _sa_update
        with get_execution_lock(execution_id):
            result = self._db.execute(
                _sa_update(Execution)
                .where(Execution.id == execution_id, Execution.status == "queued")
                .values(status="running")
            )
            self._db.commit()
            return result.rowcount > 0

    @staticmethod
    def _ensure_dir(path: str) -> str:
        Path(path).mkdir(parents=True, exist_ok=True)
        return path

    def _start_healing(self, execution_id: int, case_ids: list[int]) -> None:
        """启动后台自愈线程 — 重新启动浏览器，逐 failed case 触发 Case 级 HealRound"""
        def _heal():
            from app.db.database import SessionLocal
            from app.services.heal_service import HealRoundService
            from app.models.execution import Execution

            db = SessionLocal()
            try:
                # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 Step/Runtime/HealRecord 写入
                from app.utils.seal_guard import guard_not_sealed
                guard_not_sealed(db, execution_id)
                logger.info("开始自愈: execution_id=%s", execution_id)
                # 按 case 分组 failed steps（P0-8：Heal 触发单位是 Case，禁止逐 step 触发）
                failed_steps = (
                    db.query(ExecutionStep)
                    .filter(
                        ExecutionStep.execution_id == execution_id,
                        ExecutionStep.status == "failed",
                    )
                    .order_by(ExecutionStep.case_id, ExecutionStep.step_index)
                    .all()
                )
                failed_case_ids: list[int] = []
                for st in failed_steps:
                    if st.case_id not in failed_case_ids:
                        failed_case_ids.append(st.case_id)
                if not failed_case_ids:
                    logger.info("无失败用例，跳过自愈")
                    ExecutionFinalizer(db).seal(execution_id, "completed", _tr.NORMAL_SUCCESS)
                    return

                # 获取项目 ID + Manifest 冻结环境（P1-1：禁止回读 Project 当前值）
                exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                project_id = exec_row.project_id if exec_row else None
                if not project_id:
                    logger.error("无法获取项目 ID: execution_id=%s", execution_id)
                    return

                from app.utils.url_builder import build_target_url
                env = _env_from_manifest(_load_manifest(exec_row))
                heal_target_url = build_target_url(env["target_url"], env["test_path"])
                heal_browser_type = env["browser_type"]
                heal_headless = env["headless"]

                # 启动浏览器
                async def _heal_async():
                    from playwright.async_api import async_playwright
                    from app.utils.url_policy import UrlPolicy, install_network_policy

                    heal_service = HealRoundService(db)
                    healed = 0
                    still_failed = 0

                    policy = UrlPolicy(
                        heal_target_url,
                        allowed_hosts=env["allowed_hosts"],
                        allowed_ports=env["allowed_ports"],
                    )

                    async with async_playwright() as pw:
                        browser = await _resolve_launcher(pw, heal_browser_type).launch(headless=heal_headless)
                        context = await browser.new_context(
                            viewport={"width": 1920, "height": 1080},
                            service_workers="block",
                        )
                        await install_network_policy(context, policy)
                        page = await context.new_page()
                        page.set_default_timeout(settings.PLAYWRIGHT_TIMEOUT)

                        try:
                            await page.goto(heal_target_url, wait_until="networkidle")
                        except Exception as e:
                            logger.warning("自愈导航失败: %s，继续尝试修复", e)

                        for case_id in failed_case_ids:
                            if self._stop_requested(db, execution_id):
                                logger.info("自愈被手动停止: execution_id=%s", execution_id)
                                break

                            try:
                                result = await heal_service.heal_case(
                                    execution_id=execution_id,
                                    case_id=case_id,
                                    project_id=project_id,
                                    page=page,
                                    platform="web",
                                )
                                if result.retry_status == "success":
                                    healed += 1
                                else:
                                    still_failed += 1
                            except Exception:
                                logger.exception("自愈异常: case_id=%s", case_id)
                                still_failed += 1

                        await context.close()
                        await browser.close()

                    # 终态收敛（统一经 ExecutionFinalizer）：stop_requested → stopped；
                    # 否则 completed（counters 由 Resolver 重算）
                    if self._stop_requested(db, execution_id):
                        ExecutionFinalizer(db).seal_stopped(execution_id)
                    else:
                        ExecutionFinalizer(db).seal(execution_id, "completed", _tr.NORMAL_SUCCESS)

                    logger.info(
                        "自愈完成: execution_id=%s healed=%s still_failed=%s",
                        execution_id, healed, still_failed,
                    )

                asyncio.run(_heal_async())

            except Exception:
                logger.exception("自愈过程异常")
                try:
                    ExecutionFinalizer(db).seal(execution_id, "failed", _tr.EXECUTION_FAILED)
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
        self._step_actions: dict[int, str] = {}
        self._screenshot_dir = f"uploads/screenshots/{execution_id}/{case_id}"
        Path(self._screenshot_dir).mkdir(parents=True, exist_ok=True)
        # 该用例已物化的步骤总数：用于识别"最后一步"，让完全通过用例保留终态 after 作为成功证据
        self._total_steps = (
            self._db.query(ExecutionStep.id)
            .filter(
                ExecutionStep.execution_id == execution_id,
                ExecutionStep.case_id == case_id,
            )
            .count()
        )

    async def on_step_before(self, step_no: int, action: str, target: str, value: str) -> None:
        """步骤执行前：记录时间 + 截图 before + 创建/更新 DB 记录"""
        self._step_times[step_no] = time.time()
        self._step_actions[step_no] = action

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
        """步骤执行后：计算耗时 + 截图 + 更新 DB 记录

        截图策略（省磁盘资源）：仅保留失败步骤的截图。
          - 通过步骤：其 before 无价值 → 删除已落盘的 before 文件并清空记录，不再拍 after；
          - 失败步骤：保留 on_step_before 落盘的 before（上一状态），补拍 after（报错页面）。
        """
        start = self._step_times.get(step_no, time.time())
        duration_ms = int((time.time() - start) * 1000)

        screenshot_after = ""
        if status != "passed":
            try:
                path = f"{self._screenshot_dir}/step_{step_no}_after.jpg"
                await self._page.screenshot(path=path, type="jpeg", quality=80, full_page=False)
                screenshot_after = path
            except Exception as e:
                logger.warning("截图 after 失败: %s", e)
        else:
            # 通过步骤：非最后一步的 before 无价值 → 删除文件并清库；
            # 最后一步补拍 after（终态）作为该用例成功证据。
            try:
                os.remove(f"{self._screenshot_dir}/step_{step_no}_before.jpg")
            except OSError:
                pass
            if step_no == self._total_steps:
                try:
                    path = f"{self._screenshot_dir}/step_{step_no}_after.jpg"
                    await self._page.screenshot(path=path, type="jpeg", quality=80, full_page=False)
                    screenshot_after = path
                except Exception as e:
                    logger.warning("截图 after(终态) 失败: %s", e)

        # 更新 DB
        update_data = {
            "status": "success" if status == "passed" else "failed",
            "screenshot_after": screenshot_after,
            "duration_ms": duration_ms,
        }
        if status == "passed":
            # 通过步骤的 before 无价值，清空记录
            update_data["screenshot_before"] = ""
        if status == "failed" and error_msg:
            # 失败分类（钉死）：超时且非断言不匹配 → element_wait_timeout；
            # assert_ 非超时（文本不匹配）→ business_assertion_failed；其他 → element_not_found
            error_type, exception_type = self._classify_web_error(
                self._step_actions.get(step_no, ""), error_msg
            )
            update_data["error_message"] = error_msg[:500]
            update_data["error_type"] = error_type
            update_data["exception_type"] = exception_type
            update_data["log_output"] = f"[FAIL] step {step_no}: {error_msg[:500]}"
        else:
            update_data["log_output"] = f"[PASS] step {step_no}: {duration_ms}ms"

        self._upsert_step(step_no, update_data)

    @staticmethod
    def _classify_web_error(action: str, error_msg: str) -> tuple[str, str]:
        """Web 步骤失败 error_type 分类（供 Resolver 归因，禁止业务平台互换）"""
        m = (error_msg or "").lower()
        is_timeout = ("timeout" in m) and not any(
            k in m for k in ("expected to contain", "expected to be visible", "resolved to")
        )
        if is_timeout:
            return "element_wait_timeout", "TimeoutError"
        if str(action).startswith("assert_"):
            return "business_assertion_failed", "AssertionError"
        return "element_not_found", "Error"

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

def _build_namespace(safe: SafePlaywright, hooks: _MonitorHooks) -> dict:
    """构建受限执行命名空间

    仅注入白名单内置函数 + SafePlaywright 实例 + 监控钩子。
    不注入原生 page / browser / context / os / subprocess 等危险对象，
    也不注入 json / time / asyncio / datetime 等完整标准库模块
    （P1-3 收口：禁止完整标准库模块进入 AI 命名空间）。
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
        "safe": safe,
        "__monitor_before": hooks.on_step_before,
        "__monitor_after": hooks.on_step_after,
        "print": lambda *a, **kw: logger.info(" ".join(str(x) for x in a)),
    }
