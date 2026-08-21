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

logger = logging.getLogger("autopilot.appium")

# ── 受限命名空间白名单（与 PlaywrightService 一致） ──
ALLOWED_BUILTINS = frozenset({
    "len", "str", "range", "int", "float", "bool",
    "list", "dict", "tuple", "set", "print", "isinstance",
    "type", "enumerate", "zip", "map", "filter", "sorted",
    "min", "max", "sum", "abs", "round", "any", "all",
    "True", "False", "None", "Exception", "ValueError", "TypeError",
    "__import__",
})

from app.services.execution_state import set_stop_flag, clear_stop_flag, is_stopped


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
            self._update_execution_status(execution_id, "failed")

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

        # 构建 Appium 连接参数
        desired_caps = {
            "platformName": "Android",
            "automationName": config.get("automation_engine", "UiAutomator2"),
            "noReset": True,
            "autoGrantPermissions": True,
        }
        # 只添加非空配置项
        if config.get("app_package"):
            desired_caps["appPackage"] = config["app_package"]
        if config.get("app_activity"):
            desired_caps["appActivity"] = config["app_activity"]
        if config.get("device_name"):
            desired_caps["deviceName"] = config["device_name"]
        if config.get("platform_version"):
            desired_caps["platformVersion"] = config["platform_version"]

        appium_url = config.get("appium_server_url", settings.APPIUM_URL)

        try:
            driver = appium_webdriver.Remote(appium_url, desired_caps)
            driver.implicitly_wait(settings.APPIUM_TIMEOUT / 1000.0)

            passed = 0
            failed = 0

            for case_id in case_ids:
                if is_stopped(execution_id):
                    logger.info("Appium 执行被手动停止: execution_id=%s", execution_id)
                    break

                try:
                    success = self._execute_case(
                        driver, execution_id, case_id
                    )
                    if success:
                        passed += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                    logger.exception("Appium 用例执行异常: case_id=%s", case_id)

            # 更新执行统计
            self._update_execution(execution_id, passed, failed)

            # 关闭 driver
            try:
                driver.quit()
            except Exception:
                pass

            # 如果有失败步骤，进入自愈阶段
            if failed > 0:
                self._update_execution_status(execution_id, "healing")
                self._start_healing(execution_id, case_ids)
            else:
                self._update_execution_status(execution_id, "completed")

        except Exception:
            logger.exception("Appium 连接/启动异常")
            self._update_execution_status(execution_id, "failed")

    # ═══════════════════════════════════════════════
    # 单条用例执行（同步）
    # ═══════════════════════════════════════════════

    def _execute_case(
        self, driver, execution_id: int, case_id: int
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

        # 4. 初始化步骤记录
        self._init_steps(execution_id, case_id)

        # 5. 构建沙箱 + 执行（同步）
        hooks = _SyncMonitorHooks(self._db, execution_id, case_id, driver)
        namespace = _build_sync_namespace(driver, hooks)

        try:
            exec(code, namespace)
        except Exception as e:
            logger.error("Appium 代码编译失败: case_id=%s, %s", case_id, e)
            return False

        run_test = namespace.get("run_test")
        if not run_test:
            logger.error("Appium 代码缺少 run_test 函数: case_id=%s", case_id)
            return False

        try:
            result = run_test(driver)
            success = result.get("success", False) if isinstance(result, dict) else False
            return success
        except Exception:
            logger.exception("Appium 用例执行异常: case_id=%s", case_id)
            return False

    # ═══════════════════════════════════════════════
    # 辅助方法（与 PlaywrightService 一致）
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
        try:
            exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
            if exec_row:
                exec_row.passed_cases = passed
                exec_row.failed_cases = failed
                self._db.commit()
        except Exception:
            logger.exception("Appium 更新执行统计失败: execution_id=%s", execution_id)

    def _update_execution_status(self, execution_id: int, status: str) -> None:
        """更新执行状态"""
        try:
            exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
            if exec_row:
                exec_row.status = status
                if status in ("completed", "failed", "stopped"):
                    exec_row.end_time = datetime.utcnow()
                self._db.commit()
        except Exception:
            logger.exception("Appium 更新执行状态失败: execution_id=%s, status=%s", execution_id, status)

    @staticmethod
    def _ensure_dir(path: str) -> str:
        Path(path).mkdir(parents=True, exist_ok=True)
        return path

    def _start_healing(self, execution_id: int, case_ids: list[int]) -> None:
        """启动后台自愈线程 — 重新连接 Appium，逐个修复失败步骤（同步）"""
        def _heal():
            from app.db.database import SessionLocal
            from app.services.heal_service import HealService
            from appium import webdriver as appium_webdriver

            db = SessionLocal()
            try:
                logger.info("Appium 自愈开始: execution_id=%s", execution_id)
                failed_steps = (
                    db.query(ExecutionStep)
                    .filter(
                        ExecutionStep.execution_id == execution_id,
                        ExecutionStep.status == "failed",
                    )
                    .all()
                )
                if not failed_steps:
                    logger.info("Appium 无失败步骤，跳过自愈")
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
                    logger.error("Appium 无法获取项目 ID: execution_id=%s", execution_id)
                    return

                # 从 project.config_json 读取配置
                project = db.query(Project).filter(Project.id == project_id).first()
                config = {}
                if project and project.config_json:
                    config = json.loads(project.config_json) if isinstance(project.config_json, str) else project.config_json

                # 连接 Appium
                desired_caps = {
                    "platformName": "Android",
                    "automationName": config.get("automation_engine", "UiAutomator2"),
                    "noReset": True,
                    "autoGrantPermissions": True,
                }
                if config.get("app_package"):
                    desired_caps["appPackage"] = config["app_package"]
                if config.get("app_activity"):
                    desired_caps["appActivity"] = config["app_activity"]
                if config.get("device_name"):
                    desired_caps["deviceName"] = config["device_name"]
                if config.get("platform_version"):
                    desired_caps["platformVersion"] = config["platform_version"]
                appium_url = config.get("appium_server_url", settings.APPIUM_URL)
                driver = appium_webdriver.Remote(appium_url, desired_caps)
                driver.implicitly_wait(settings.APPIUM_TIMEOUT / 1000.0)

                heal_service = HealService(db)
                healed = 0
                still_failed = 0

                for step in failed_steps:
                    if is_stopped(execution_id):
                        logger.info("Appium 自愈被手动停止: execution_id=%s", execution_id)
                        break

                    try:
                        # HealService.try_heal 接受 page 参数，但同步执行时不需要 page
                        # 这里传递 driver 用于截图等操作
                        import asyncio as _asyncio
                        success = _asyncio.run(heal_service.try_heal(
                            execution_id=execution_id,
                            step=step,
                            page=driver,  # 同步 driver 作为 page 参数传递
                            project_id=project_id,
                            max_retries=settings.MAX_HEAL_RETRY,
                            platform="android",
                        ))
                        if success:
                            healed += 1
                        else:
                            still_failed += 1
                    except Exception:
                        logger.exception("Appium 自愈异常: step_id=%s", step.id)
                        still_failed += 1

                try:
                    driver.quit()
                except Exception:
                    pass

                # 更新执行统计
                exec_row = db.query(Execution).filter(Execution.id == execution_id).first()
                if exec_row:
                    exec_row.passed_cases = (exec_row.passed_cases or 0) + healed
                    exec_row.failed_cases = still_failed
                    exec_row.status = "completed"
                    exec_row.end_time = datetime.utcnow()
                    db.commit()

                logger.info(
                    "Appium 自愈完成: execution_id=%s healed=%s still_failed=%s",
                    execution_id, healed, still_failed,
                )

            except Exception:
                logger.exception("Appium 自愈过程异常")
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
        """步骤执行后：计算耗时 + 截图 after + 更新 DB 记录"""
        start = self._step_times.get(step_no, time.time())
        duration_ms = int((time.time() - start) * 1000)

        # 截图 after
        screenshot_after = ""
        try:
            path = f"{self._screenshot_dir}/step_{step_no}_after.png"
            self._driver.save_screenshot(path)
            screenshot_after = path
        except Exception as e:
            logger.warning("Appium 截图 after 失败: %s", e)

        # 更新 DB
        update_data = {
            "status": "success" if status == "passed" else "failed",
            "screenshot_after": screenshot_after,
            "duration_ms": duration_ms,
        }
        if status == "failed" and error_msg:
            # 组合 exception_type 与 error_message，确保分类逻辑能匹配异常类型前缀
            if exception_type:
                combined = f"{exception_type}: {error_msg[:490]}"
                update_data["error_message"] = combined[:500]
            else:
                update_data["error_message"] = error_msg[:500]
            update_data["exception_type"] = exception_type[:100] if exception_type else ""
            update_data["log_output"] = f"[FAIL] step {step_no}: {update_data['error_message'][:500]}"
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

    注入 `driver` 而非 `page`，供 Android 用户代码使用。
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

    from appium.webdriver.common.appiumby import AppiumBy

    return {
        "__builtins__": safe_builtins,
        "driver": driver,
        "AppiumBy": AppiumBy,
        "json": json,
        "time": time,
        "datetime": datetime,
        "__monitor_before": hooks.on_step_before,
        "__monitor_after": hooks.on_step_after,
        "print": lambda *a, **kw: logger.info(" ".join(str(x) for x in a)),
    }