"""Appium 安全执行引擎 — 同步沙箱执行 + 步骤监控 + 截图 + 自愈调度

Android 使用同步 WebDriver 模型：
    def run_test(driver):
        ...

与 PlaywrightService 的异步模型（async def run_test(page)）完全独立。
两种服务使用相同的后台 Thread 启动机制，但内部执行方式不同：
    - Web:  asyncio.run(playwright_service.execute(...))
    - Android: appium_service.execute(...)  # 直接在线程中运行
"""

import json
import logging
import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.models.project import Project
from app.utils.code_validator import CodeValidator
from app.utils.appium_code_injector import AppiumCodeInjector
from app.exceptions import SecurityException
from app.services.execution_finalizer import ExecutionFinalizer
from app.utils import terminal_reason as _tr

logger = logging.getLogger("autopilot.appium")

# ── 受限命名空间白名单（与 PlaywrightService 一致） ──
ALLOWED_BUILTINS = frozenset({
    "len", "str", "range", "int", "float", "bool",
    "list", "dict", "tuple", "set", "print", "isinstance",
    "type", "enumerate", "zip", "map", "filter", "sorted",
    "min", "max", "sum", "abs", "round", "any", "all",
    "True", "False", "None", "Exception", "ValueError", "TypeError",
})

from app.services.execution_state import set_stop_flag, clear_stop_flag, is_stopped, db_stop_requested, generate_worker_id


class AppiumService:
    """Appium 安全执行引擎 — Android UI 自动化

    执行流程:
      1. 创建 Appium WebDriver 连接（UiAutomator2）
      2. 逐条执行用例：
         a. 获取最新代码 → 安全校验 → AST 注入监控
         b. 构建受限命名空间（白名单 builtins）
         c. 在沙箱中同步执行 run_test(driver)
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
    # 主入口 — 在后台线程中运行（同步执行）
    # ═══════════════════════════════════════════════

    def execute(
        self,
        project_id: int,
        case_ids: list[int],
        execution_id: int,
        mode: str = "headless",
    ) -> None:
        """在后台线程中运行整个执行流程（同步，无需 asyncio）

        此方法由 Orchestrator 在独立线程中直接调用。
        """
        try:
            self._execute_sync(project_id, case_ids, execution_id, mode)
        except Exception:
            logger.exception("Appium 执行异常: execution_id=%s", execution_id)
            ExecutionFinalizer(self._db).seal(execution_id, "failed", _tr.EXECUTION_FAILED)

    def _execute_sync(
        self,
        project_id: int,
        case_ids: list[int],
        execution_id: int,
        mode: str,
    ) -> None:
        """同步执行主循环"""
        from appium import webdriver as appium_webdriver

        # 从 project.config_json 读取 Android 配置
        project = self._db.query(Project).filter(Project.id == project_id).first()
        config = {}
        if project and project.config_json:
            config = json.loads(project.config_json) if isinstance(project.config_json, str) else project.config_json

        # 统一构建 desired_caps（含 extra_caps 透传 + 强制 skip caps，见 utils/appium_caps）
        from app.utils.appium_caps import build_caps
        desired_caps = build_caps(config)

        appium_url = config.get("appium_server_url", settings.APPIUM_URL)

        # 原子状态机：queued → running（条件更新；影响行数 0 则放弃启动，
        # 可能已被 Stop 或 Recovery 抢先改写，杜绝 stopped→running 回跳）
        if not self._mark_running_if_queued(execution_id):
            logger.info("Appium 执行已被 Stop/Recovery 抢先改写，放弃启动: execution_id=%s", execution_id)
            return
        self._update_execution(execution_id)

        try:
            # 4.1.0 的 Remote 构造：caps 须经 AppiumOptions 传入（options 关键字），
            # 直接传 dict 会被当作 keep_alive 导致 caps 丢失且新版 selenium 报 TypeError
            from appium.webdriver.webdriver import AppiumOptions
            opts = AppiumOptions()
            opts.load_capabilities(desired_caps)
            driver = appium_webdriver.Remote(appium_url, options=opts)
            driver.implicitly_wait(settings.APPIUM_TIMEOUT / 1000.0)

            any_failure = False

            for case_id in case_ids:
                if self._stop_requested(self._db, execution_id):
                    logger.info("Appium 执行被手动停止: execution_id=%s", execution_id)
                    break

                # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 Step/Runtime 写入
                from app.utils.seal_guard import guard_not_sealed
                guard_not_sealed(self._db, execution_id)

                try:
                    success = self._execute_case(
                        driver, execution_id, case_id
                    )
                    if not success:
                        any_failure = True
                except Exception:
                    any_failure = True
                    logger.exception("Appium 用例执行异常: case_id=%s", case_id)

                # 逐用例心跳保活（counters 为 Seal 时 Resolver 重算的 Derived Cache）
                self._update_execution(execution_id)

            # 心跳保活
            self._update_execution(execution_id)

            # 关闭 driver
            try:
                driver.quit()
            except Exception:
                pass

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
            logger.exception("Appium 连接/启动异常")
            ExecutionFinalizer(self._db).seal(execution_id, "failed", _tr.EXECUTION_FAILED)

    # ═══════════════════════════════════════════════
    # 单条用例执行（同步）
    # ═══════════════════════════════════════════════

    def _execute_case(
        self, driver, execution_id: int, case_id: int
    ) -> bool:
        """执行单条用例，返回是否成功"""
        # 1. 获取冻结的活跃代码（统一走 ExecutionCodeResolver，禁止 latest 后门）
        from app.services.execution_code_resolver import ExecutionCodeResolver
        gen_code = ExecutionCodeResolver(self._db).get_active_code(execution_id, case_id)
        if not gen_code:
            logger.warning("Appium 用例 %s 无有效代码，跳过", case_id)
            return False

        code = gen_code.code_content

        # 2. 安全校验
        error = CodeValidator.validate(code, platform="android")
        if error:
            raise SecurityException(f"Appium 用例 {case_id} 代码校验失败: {error}")

        # 3. AST 注入监控钩子
        try:
            code = AppiumCodeInjector.inject(code)
        except SecurityException:
            raise

        # 4. 构建沙箱 + 执行（同步）（步骤已在 Admission 物化，禁止 delete+rebuild）
        hooks = _SyncMonitorHooks(self._db, execution_id, case_id, driver)
        namespace = _build_sync_namespace(driver, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            logger.error("Appium 代码编译失败: case_id=%s, %s", case_id, e)
            self._mark_case_failed(execution_id, case_id)
            return False

        run_test = namespace.get("run_test")
        if not run_test:
            logger.error("Appium 代码缺少 run_test 函数: case_id=%s", case_id)
            self._mark_case_failed(execution_id, case_id)
            return False

        try:
            # 传入受控 DriverProxy（run_test 参数会遮蔽 namespace 全局名，
            # 必须显式传 proxy，否则 AI 拿到的是原生 driver）
            result = run_test(namespace["driver"])
            success = result.get("success", False) if isinstance(result, dict) else False
            if not success:
                self._mark_case_failed(execution_id, case_id)
            return success
        except Exception:
            logger.exception("Appium 用例执行异常: case_id=%s", case_id)
            self._mark_case_failed(execution_id, case_id)
            return False

    # ═══════════════════════════════════════════════
    # 辅助方法（与 PlaywrightService 一致）
    # ═══════════════════════════════════════════════

    def _mark_case_failed(self, execution_id: int, case_id: int, *,
                          error_type: str = "execution_failed",
                          error_message: str = "case_failed") -> None:
        """用例失败兜底：仅标记该 case 首个未终态步骤为 failed（禁止整 case 刷 failed）。

        真实步骤失败已由监控钩子按 error_type 分类记录；此处只兜底未终态步骤。
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
            logger.exception("Appium 更新执行心跳失败: execution_id=%s", execution_id)

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
            logger.exception("Appium 更新执行状态失败: execution_id=%s, status=%s", execution_id, status)

    @staticmethod
    def _ensure_dir(path: str) -> str:
        Path(path).mkdir(parents=True, exist_ok=True)
        return path

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

    def _start_healing(self, execution_id: int, case_ids: list[int]) -> None:
        """启动后台自愈线程 — 重新连接 Appium，逐 failed case 触发 Case 级 HealRound（同步）"""
        def _heal():
            from app.db.database import SessionLocal
            from app.services.heal_service import HealRoundService
            from appium import webdriver as appium_webdriver

            db = SessionLocal()
            try:
                # Seal 守卫（P0-10）：终态 Execution 拒绝任何后续 Step/Runtime/HealRecord 写入
                from app.utils.seal_guard import guard_not_sealed
                guard_not_sealed(db, execution_id)
                logger.info("Appium 自愈开始: execution_id=%s", execution_id)
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
                    logger.info("Appium 无失败用例，跳过自愈")
                    ExecutionFinalizer(db).seal(execution_id, "completed", _tr.NORMAL_SUCCESS)
                    return

                # 获取项目 ID
                exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                project_id = exec_row.project_id if exec_row else None
                if not project_id:
                    logger.error("Appium 无法获取项目 ID: execution_id=%s", execution_id)
                    return

                # 从 project.config_json 读取配置
                project = db.query(Project).filter(Project.id == project_id).first()
                config = {}
                if project and project.config_json:
                    config = json.loads(project.config_json) if isinstance(project.config_json, str) else project.config_json

                # 连接 Appium（统一构建 caps：含 extra_caps 透传 + 强制 skip，见 utils/appium_caps）
                from app.utils.appium_caps import build_caps
                desired_caps = build_caps(config)
                appium_url = config.get("appium_server_url", settings.APPIUM_URL)
                # 4.1.0 构造签名：caps 须经 AppiumOptions 传入（options 关键字）
                from appium.webdriver.webdriver import AppiumOptions
                opts = AppiumOptions()
                opts.load_capabilities(desired_caps)
                driver = appium_webdriver.Remote(appium_url, options=opts)
                driver.implicitly_wait(settings.APPIUM_TIMEOUT / 1000.0)

                heal_service = HealRoundService(db)
                healed = 0
                still_failed = 0

                for case_id in failed_case_ids:
                    if self._stop_requested(db, execution_id):
                        logger.info("Appium 自愈被手动停止: execution_id=%s", execution_id)
                        break

                    try:
                        # HealRoundService.heal_case 为 async 包装；Android rerun 内部走同步 _rerun_case_sync
                        import asyncio as _asyncio
                        result = _asyncio.run(heal_service.heal_case(
                            execution_id=execution_id,
                            case_id=case_id,
                            project_id=project_id,
                            page=driver,  # 同步 driver 作为 page 参数传递
                            platform="android",
                        ))
                        if result.retry_status == "success":
                            healed += 1
                        else:
                            still_failed += 1
                    except Exception:
                        logger.exception("Appium 自愈异常: case_id=%s", case_id)
                        still_failed += 1

                try:
                    driver.quit()
                except Exception:
                    pass

                # 终态收敛（统一经 ExecutionFinalizer）：stop_requested → stopped；
                # 否则 completed（counters 由 Resolver 重算）
                if self._stop_requested(db, execution_id):
                    ExecutionFinalizer(db).seal_stopped(execution_id)
                else:
                    ExecutionFinalizer(db).seal(execution_id, "completed", _tr.NORMAL_SUCCESS)

                logger.info(
                    "Appium 自愈完成: execution_id=%s healed=%s still_failed=%s",
                    execution_id, healed, still_failed,
                )

            except Exception:
                logger.exception("Appium 自愈过程异常")
                try:
                    ExecutionFinalizer(db).seal(execution_id, "failed", _tr.EXECUTION_FAILED)
                except Exception:
                    pass
            finally:
                db.close()

        t = threading.Thread(target=_heal, daemon=True)
        t.start()


# ═══════════════════════════════════════════════
# 同步监控钩子
# ═══════════════════════════════════════════════

class _SyncMonitorHooks:
    """注入到 Android 用户代码中的 __monitor_before / __monitor_after 钩子（同步）

    用法（由 CodeInjector 自动注入，与 Web 版本共享同一注入逻辑）:
        __monitor_before(1, "click", "com.example:id/btn", "")
        try:
            driver.find_element(...).click()
        except Exception as __ae:
            __monitor_after(1, "failed", str(__ae))
            raise
        else:
            __monitor_after(1, "passed", "")
    """

    def __init__(
        self, db: Session, execution_id: int, case_id: int, driver
    ) -> None:
        self._db = db
        self._execution_id = execution_id
        self._case_id = case_id
        self._driver = driver
        self._step_times: dict[int, float] = {}
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

    def on_step_before(self, step_no: int, action: str, target: str, value: str) -> None:
        """步骤执行前：记录时间 + 截图 before + 创建/更新 DB 记录"""
        self._step_times[step_no] = time.time()

        # 截图 before
        screenshot_before = ""
        try:
            path = f"{self._screenshot_dir}/step_{step_no}_before.png"
            self._driver.save_screenshot(path)
            screenshot_before = path
        except Exception as e:
            logger.warning("Appium 截图 before 失败: %s", e)

        # 更新 DB
        self._upsert_step(step_no, {
            "action": action,
            "target_selector": target,
            "input_value": value,
            "screenshot_before": screenshot_before,
            "status": "running",
        })

    def on_step_after(self, step_no: int, status: str, error_msg: str = "", exception_type: str = "") -> None:
        """步骤执行后：计算耗时 + 截图 + 更新 DB 记录

        截图策略（省磁盘资源）：仅保留失败步骤的截图。
          - 通过步骤：其 before 无价值 → 删除已落盘的 before 文件并清空记录，不再拍 after；
          - 失败步骤：保留 on_step_before 落盘的 before，补拍 after（报错页面）。
        """
        start = self._step_times.get(step_no, time.time())
        duration_ms = int((time.time() - start) * 1000)

        screenshot_after = ""
        if status != "passed":
            try:
                path = f"{self._screenshot_dir}/step_{step_no}_after.png"
                self._driver.save_screenshot(path)
                screenshot_after = path
            except Exception as e:
                logger.warning("Appium 截图 after 失败: %s", e)
        else:
            # 通过步骤：非最后一步的 before 无价值 → 删除文件并清库；
            # 最后一步补拍 after（终态）作为该用例成功证据。
            try:
                os.remove(f"{self._screenshot_dir}/step_{step_no}_before.png")
            except OSError:
                pass
            if step_no == self._total_steps:
                try:
                    path = f"{self._screenshot_dir}/step_{step_no}_after.png"
                    self._driver.save_screenshot(path)
                    screenshot_after = path
                except Exception as e:
                    logger.warning("Appium 截图 after(终态) 失败: %s", e)

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
            # 组合 exception_type 与 error_message，确保分类逻辑能匹配异常类型前缀
            error_type = self._classify_appium_error(exception_type, error_msg)
            if exception_type:
                combined = f"{exception_type}: {error_msg[:490]}"
                update_data["error_message"] = combined[:500]
            else:
                update_data["error_message"] = error_msg[:500]
            update_data["error_type"] = error_type
            update_data["exception_type"] = exception_type[:100] if exception_type else ""
            update_data["log_output"] = f"[FAIL] step {step_no}: {update_data['error_message'][:500]}"
        else:
            update_data["log_output"] = f"[PASS] step {step_no}: {duration_ms}ms"

        self._upsert_step(step_no, update_data)

    @staticmethod
    def _classify_appium_error(exception_type: str, error_msg: str) -> str:
        """Appium 步骤失败 error_type 分类（供 Resolver 归因，禁止业务平台互换）"""
        ex = (exception_type or "").lower()
        m = (error_msg or "").lower()
        if "staleelement" in ex or "stale element" in m:
            return "stale_element"
        if "nosuchelement" in ex or "no such element" in m:
            return "element_not_found"
        if "timeoutexception" in ex or "timeout" in m:
            return "element_wait_timeout"
        if ex.startswith("assertionerror"):
            return "business_assertion_failed"
        return "execution_failed"

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
            logger.error("Appium 更新步骤记录失败: step=%s, %s", step_no, e)
            try:
                self._db.rollback()
            except Exception:
                pass


# ═══════════════════════════════════════════════
# 同步沙箱命名空间构建
# ═══════════════════════════════════════════════

def _build_sync_namespace(driver, hooks: _SyncMonitorHooks) -> dict:
    """构建受限执行命名空间（同步版）

    namespace 只含：白名单 builtins + 受控 driver proxy + AppiumBy wrapper +
    hooks + 受控 sleep。禁止注入原生 driver 与 json/time/datetime 等完整
    标准库模块（P1-3 收口）。run_test 收到的 driver 参数为受控 DriverProxy。
    """
    import builtins as _builtins_module

    # 受限 builtins
    safe_builtins = {}
    for name in ALLOWED_BUILTINS:
        obj = getattr(_builtins_module, name, None)
        if obj is not None:
            safe_builtins[name] = obj

    safe_builtins["Exception"] = Exception
    safe_builtins["ValueError"] = ValueError
    safe_builtins["TypeError"] = TypeError

    from app.utils.appium_proxy import DriverProxy, AppiumByProxy

    driver_proxy = DriverProxy(driver)
    return {
        "__builtins__": safe_builtins,
        "driver": driver_proxy,
        "AppiumBy": AppiumByProxy,
        "sleep": lambda sec: time.sleep(sec),
        "__monitor_before": hooks.on_step_before,
        "__monitor_after": hooks.on_step_after,
        "print": lambda *a, **kw: logger.info(" ".join(str(x) for x in a)),
    }