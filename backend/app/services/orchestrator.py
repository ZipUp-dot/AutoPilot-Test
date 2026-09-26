"""测试流水线编排器 — 流程控制 + 依赖注入 + 异常隔离

设计原则:
  - 编排器是"导演"，services 是"演员"
  - 编排器只调用 service 层的公共方法，不直接操作数据库或 Playwright
  - 自愈不由编排器主动调用，由执行引擎在执行失败时自动触发（降低耦合）
  - 异常隔离：某个模块失败不导致整个流水线崩溃

使用方式（通过依赖注入）:
    orchestrator = TestOrchestrator(
        ai_service=ai_svc,
        playwright_service=pw_svc,
        report_service=report_svc,
    )
    result = await orchestrator.run_full_pipeline(project_id, case_ids, mode, batch_name)
"""

import asyncio
import logging
from typing import Any, Optional

from app.config import settings
from app.exceptions import NotFoundException, ValidationException
from app.models.generated_code import GeneratedCode
from app.services.execution_state import clear_stop_flag
from app.services import batch_generate_service as _bgs

logger = logging.getLogger("autopilot.orchestrator")


class TestOrchestrator:
    """测试流水线编排器

    通过依赖注入传入服务，便于测试和替换。
    编排器只负责流程控制，不包含具体业务逻辑。

    支持两种执行平台：
        - Web (playwright): 异步执行，后台线程中 asyncio.run()
        - Android (appium): 同步执行，后台线程中直接运行
    """

    def __init__(
        self,
        ai_service: Any = None,
        playwright_service: Any = None,
        appium_service: Any = None,
        report_service: Any = None,
        batch_generate_service: Any = None,
        admission_service: Any = None,
    ) -> None:
        """依赖注入初始化

        Args:
            ai_service: AIService 实例
            playwright_service: PlaywrightService 实例（Web 执行）
            appium_service: AppiumService 实例（Android 执行）
            report_service: ReportService 实例
            batch_generate_service: BatchGenerateService 实例；None 时使用进程内
                单例 batch_generate_service（与生成页路由双入口收敛到同一实现）。
            admission_service: ExecutionAdmissionService 实例；None 时按需自建。
        """
        self.ai_service = ai_service
        self.playwright_service = playwright_service
        self.appium_service = appium_service
        self.report_service = report_service
        self._batch_generate_service = batch_generate_service
        self._admission_service = admission_service

    @property
    def batch_gen(self) -> Any:
        """返回注入的 BatchGenerateService，缺省用进程内单例（双入口统一点）"""
        return self._batch_generate_service or _bgs.batch_generate_service

    @property
    def admission(self) -> Any:
        """返回注入的 ExecutionAdmissionService，缺省自建（请求会话分离）"""
        return self._admission_service

    def _get_executor(self, platform: str = "web") -> tuple[Any, str]:
        """根据平台类型获取对应的执行器

        Args:
            platform: "web" 或 "android"

        Returns:
            (executor_service, platform_type)
        """
        if platform == "android":
            return self.appium_service, "android"
        return self.playwright_service, "web"

    # ═══════════════════════════════════════════════
    # 完整流水线
    # ═══════════════════════════════════════════════

    async def run_full_pipeline(
        self,
        project_id: int,
        case_ids: list[int],
        mode: str = "headless",
        batch_name: str | None = None,
        platform: str = "web",
    ) -> dict:
        """完整流水线：检查 → 生成（如需）→ 执行 → 监听 → 报告

        Returns:
            { "execution_id": 1, "status": "running", "generated": 3 }
        """
        # Step 1: 检查用例是否已生成代码，未生成的先补生成
        cases_to_generate = await self._check_cases_need_generation(case_ids)
        generated_count = 0
        batch_id: str | None = None
        if cases_to_generate:
            logger.info("编排器: 部分用例尚未生成代码，全集合批量生成 %s 条", len(case_ids))
            # 全集合 all-or-none：批量生成必须覆盖【全部】请求 case（非仅缺失子集），
            # 使 Batch 集合与后续 Admission 的 case_ids 集合完全一致（禁止子集偷跑）。
            try:
                summary = await asyncio.to_thread(
                    self._generate_batch_sync, project_id, case_ids
                )
            except (NotFoundException, ValidationException):
                raise  # 域异常（项目/用例校验等）按原类型继续向上抛
            except Exception as e:  # noqa: BLE001
                logger.warning("编排器: 批量生成基础设施异常，整体不创建执行: %s", e)
                raise ValidationException(
                    f"代码生成失败，整体不创建执行: {e}"
                ) from e
            if summary.get("status") != "completed" or (summary.get("failed", 0) or summary.get("skipped", 0)):
                logger.warning("编排器: 批量生成未全部成功，整体不创建执行")
                raise ValidationException(
                    "代码生成未完全成功（失败/跳过 >0），整体不创建执行"
                )
            generated_count = summary.get("success", 0)
            batch_id = summary.get("batch_id")
        else:
            logger.info("编排器: 全部用例已有代码，走 effective_code 入口")

        # Step 1.5: 执行前目标环境健康检查（防止目标不可达时集体失败 + 无意义自愈）
        check_error = await self._pre_execution_check(project_id, platform)
        if check_error:
            raise ValidationException(f"执行前环境检查失败: {check_error}")

        return await self._admit_and_launch(
            project_id, case_ids, mode, batch_name, platform,
            batch_id=batch_id, generated_count=generated_count,
        )

    # ═══════════════════════════════════════════════
    # 仅生成
    # ═══════════════════════════════════════════════

    async def run_generate_only(
        self, project_id: int, case_ids: list[int]
    ) -> dict:
        """仅生成代码，不执行（异常隔离：单个失败不影响其他）"""
        logger.info("编排器: 批量生成代码 %s 条", len(case_ids))
        results = self.ai_service.generate_batch(project_id, case_ids)
        generated = [r for r in results if r.get("status") == "success"]
        failed = [r for r in results if r.get("status") != "success"]
        return {
            "generated_count": len(generated),
            "failed_count": len(failed),
            "results": results,
        }

    # ═══════════════════════════════════════════════
    # 仅执行
    # ═══════════════════════════════════════════════

    async def run_execute_only(
        self,
        project_id: int,
        case_ids: list[int],
        mode: str = "headless",
        batch_name: str | None = None,
        platform: str = "web",
    ) -> dict:
        """仅执行（假设代码已生成），启动执行 + 监听报告"""
        # 执行前目标环境健康检查（防止目标不可达时集体失败 + 无意义自愈）
        check_error = await self._pre_execution_check(project_id, platform)
        if check_error:
            raise ValidationException(f"执行前环境检查失败: {check_error}")

        return await self._admit_and_launch(
            project_id, case_ids, mode, batch_name, platform,
            batch_id=None, generated_count=0,
        )

    # ═══════════════════════════════════════════════
    # 内部方法 — Admission 统一入口 + Pre-start Drift Guard
    # ═══════════════════════════════════════════════

    async def _admit_and_launch(
        self,
        project_id: int,
        case_ids: list[int],
        mode: str,
        batch_name: str | None,
        platform: str,
        batch_id: str | None = None,
        retry_from_execution_id: int | None = None,
        generated_count: int = 0,
    ) -> dict:
        """统一 Admission → materialize → Pre-start Drift Guard → 后台线程 → 监听。

        Admission 失败 → raise ValidationException（带逐 case 原因），不建 Execution。
        Drift Guard 失败 → Execution 置 failed + 未启动 case 置 skipped，不启线程。
        """
        result = self.admission.admit(
            project_id, case_ids,
            batch_id=batch_id, retry_from_execution_id=retry_from_execution_id,
            execution_mode=mode,
        )
        if not result.ok:
            details = "; ".join(f"case {k}: {v}" for k, v in sorted(result.errors.items()))
            raise ValidationException(f"Execution 校验失败: {details}")

        execution_id = self.admission.materialize(result, batch_name=batch_name)
        clear_stop_flag(execution_id)

        # Pre-start Drift Guard：Execution 已 Admission 但未启动时，发现 TestCase 已被
        # 修改 → 拒绝启动旧 Execution（Manifest immutable + runtime_state 冻结）。
        drifted = self._guard_pre_start_drift(execution_id, result.manifest)
        if drifted:
            logger.warning(
                "编排器: Pre-start Drift Guard 拦截 execution_id=%s drifted=%s",
                execution_id, drifted,
            )
            return {
                "execution_id": execution_id,
                "status": "failed",
                "pre_start_drift": True,
                "drifted_cases": drifted,
                "generated": generated_count,
            }

        _, platform_type = self._get_executor(platform)
        import threading

        def _run():
            from app.db.database import SessionLocal
            db_session = SessionLocal()
            try:
                if platform_type == "android":
                    from app.services.appium_service import AppiumService
                    svc = AppiumService(db_session)
                    svc.execute(project_id, case_ids, execution_id, mode)
                else:
                    from app.services.playwright_service import PlaywrightService
                    svc = PlaywrightService(db_session)
                    svc.execute(project_id, case_ids, execution_id, mode)
            except Exception:
                logger.exception("后台执行线程异常: execution_id=%s", execution_id)
                try:
                    from app.services.execution_finalizer import ExecutionFinalizer
                    from app.utils import terminal_reason as _tr
                    ExecutionFinalizer(db_session).seal(
                        execution_id, "failed", _tr.EXECUTION_FAILED
                    )
                except Exception:
                    pass
            finally:
                db_session.close()
                clear_stop_flag(execution_id)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        asyncio.create_task(self._monitor_and_generate_report(execution_id))

        logger.info("编排器: 执行已启动 execution_id=%s cases=%s", execution_id, len(case_ids))
        return {
            "execution_id": execution_id,
            "status": "running",
            "generated": generated_count,
        }

    def _guard_pre_start_drift(self, execution_id: int, manifest_cases: list[dict]) -> list[int]:
        """重算当前 TestCase hash 与 Manifest steps_hash 比对，不一致拒绝启动。

        任一不一致 → 将 Execution 置 failed + 全部未启动 ExecutionStep 置
        skipped(skip_reason=pre_start_drift, error_type=pre_start_drift)。
        Execution Start Coverage 计 0（保留 admitted 未 started 事实）。

        Returns:
            发生漂移的 case_id 列表（空 = 全部一致，可正常启动）。
        """
        import json
        from app.db.database import SessionLocal
        from app.models.test_case import TestCase
        from app.utils.step_canonicalizer import hash_steps

        expected = {c["case_id"]: c["steps_hash"] for c in manifest_cases}
        # 复用 Admission 会话（同一请求事务；测试环境由 get_db override 绑定测试库），
        # 无 admission 实例（独立单测场景）时回退自建 SessionLocal。
        db = getattr(getattr(self, "admission", None), "_db", None)
        owns_session = db is None
        if owns_session:
            db = SessionLocal()
        try:
            drifted: list[int] = []
            for cid, expected_hash in expected.items():
                case = db.query(TestCase).filter(TestCase.id == cid).first()
                actual_hash = None
                if case and case.steps:
                    try:
                        actual_hash = hash_steps(json.loads(case.steps))
                    except (TypeError, ValueError):
                        actual_hash = None
                if actual_hash != expected_hash:
                    drifted.append(cid)
            if drifted:
                # 统一经 ExecutionFinalizer 收敛（Step 改写 + counters 重算 + 幂等）
                from app.services.execution_finalizer import ExecutionFinalizer
                from app.utils import terminal_reason as _tr
                ExecutionFinalizer(db).seal(
                    execution_id, "failed", _tr.EXECUTION_FAILED,
                    pending_skip_reason="pre_start_drift",
                )
            return drifted
        finally:
            if owns_session:
                db.close()

    # ═══════════════════════════════════════════════
    # 内部方法 — 状态监听 + 自动生成报告
    # ═══════════════════════════════════════════════

    async def _pre_execution_check(self, project_id: int, platform: str = "web") -> Optional[str]:
        """执行前目标环境健康检查

        目标网站 / Appium Server 不可达时提前拦截，避免创建执行后大量用例
        集体失败，进而触发无意义的自愈 AI 调用（烧 Token）。

        Returns:
            None 表示检查通过；字符串为错误原因
        """
        if not settings.PRE_EXECUTION_CHECK:
            return None

        try:
            from app.db.database import SessionLocal
            db = SessionLocal()
            try:
                from app.models.project import Project
                project = db.query(Project).filter(Project.id == project_id).first()
            finally:
                db.close()

            if not project:
                return f"项目 {project_id} 不存在"

            import httpx
            from app.utils.url_builder import build_target_url
            if platform == "android":
                url = f"{settings.APPIUM_URL}/status"
                label = "Appium Server"
            else:
                url = build_target_url(project.target_url or "", project.test_path or "/")
                label = "目标网站"
                if not url:
                    return None  # 无目标 URL，跳过检查

                # SSRF 入口校验：非法/越权目标 URL 直接拒绝执行
                # （P1-1：pre-check 属 Admission 前上下文，读 Project 当前值）
                import json
                from app.utils.url_policy import validate_target_url
                try:
                    config_json = json.loads(project.config_json) if project.config_json else None
                except (TypeError, ValueError):
                    config_json = None
                url_error = validate_target_url(url, config_json=config_json)
                if url_error:
                    return f"目标 URL 校验失败: {url_error}"

            try:
                async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                logger.info("执行前健康检查: %s -> HTTP %s", url, resp.status_code)
            except Exception as e:
                return f"{label}不可达: {str(e)[:200]}"
        except Exception as e:
            # 检查过程自身异常不阻塞执行（异常隔离）
            logger.warning("执行前健康检查异常（忽略）: %s", e)
            return None

        return None

    async def _monitor_and_generate_report(self, execution_id: int) -> None:
        """监听 execution 状态，到达任一终态时生成对应 report_type 的报告

        每 2 秒轮询一次，最多持续 30 分钟。
        四种终态都生成报告（fire-and-forget，异常仅 log）：
          completed→full、stopped→partial、failed→diagnostic、interrupted→interrupted
        （report_type 由 ReportService 依据 execution.status 决定，禁止 monitor 自定义）。
        """
        try:
            max_polls = 900  # 30 分钟
            for _ in range(max_polls):
                await asyncio.sleep(2)
                try:
                    execution = self._get_execution_status(execution_id)
                except Exception:
                    continue  # DB 查询异常则继续等待

                if execution is None:
                    continue

                status = execution.get("status", "")
                if status in ("completed", "stopped", "failed", "interrupted"):
                    logger.info("编排器: execution_id=%s 状态=%s，自动生成报告", execution_id, status)
                    try:
                        # 使用独立的 DB 会话生成报告（原会话可能已关闭）
                        from app.db.database import SessionLocal
                        db = SessionLocal()
                        try:
                            from app.services.report_service import ReportService
                            report_svc = ReportService(db)
                            report_svc.generate(execution_id)
                            logger.info("编排器: 报告生成完成 execution_id=%s", execution_id)
                        finally:
                            db.close()
                    except Exception as e:
                        # 另一 owner 已在生成（generating 未超时）→ conflict，跳过即可；
                        # 其余生成失败仅记录，由后续 monitor/手动触发重试
                        logger.error("编排器: 报告生成失败 execution_id=%s: %s", execution_id, e)
                    break
        except asyncio.CancelledError:
            logger.info("编排器: 监听协程被取消 execution_id=%s", execution_id)
        except Exception:
            logger.exception("编排器: 监听异常 execution_id=%s", execution_id)

    def _get_execution_status(self, execution_id: int) -> Optional[dict]:
        """通过 PlaywrightService 绑定的 DB 查询执行状态

        编排器不直接操作 DB，通过 service 的 query 方法间接获取。
        """
        try:
            from app.db.database import SessionLocal
            db = SessionLocal()
            try:
                from app.models.execution import Execution
                ex = db.query(Execution).filter(Execution.id == execution_id).first()
                if ex:
                    return {"status": ex.status}
                return None
            finally:
                db.close()
        except Exception:
            return None

    # ═══════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════

    def _generate_batch_sync(self, project_id: int, case_ids: list[int]) -> dict:
        """同步执行批量生成并等待整个 Batch 完成 Finalization（经 asyncio.to_thread 调用）。

        与生成页路由统一走 BatchGenerateService 单例；返回冻结后的 terminal snapshot。
        """
        svc = self.batch_gen
        batch_id = svc.create_job(project_id, case_ids)
        return svc.wait_frozen(batch_id)

    async def _check_cases_need_generation(
        self, case_ids: list[int]
    ) -> list[int]:
        """检查哪些用例尚未生成有效代码

        编排器不直接查询 DB，通过 AI service 的 DB 连接间接查询。
        """
        try:
            from app.db.database import SessionLocal
            db = SessionLocal()
            try:
                need_gen = []
                for cid in case_ids:
                    gen = (
                        db.query(GeneratedCode)
                        .filter(GeneratedCode.case_id == cid, GeneratedCode.is_valid == 1)
                        .first()
                    )
                    if not gen:
                        need_gen.append(cid)
                return need_gen
            finally:
                db.close()
        except Exception:
            # 异常隔离：DB 查询失败不阻塞流水线，返回全部需要生成
            logger.warning("审查代码状态失败，默认全部需要生成")
            return list(case_ids)
