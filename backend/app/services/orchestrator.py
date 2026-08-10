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

from app.models.generated_code import GeneratedCode

logger = logging.getLogger("autopilot.orchestrator")


class TestOrchestrator:
    """测试流水线编排器

    通过依赖注入传入服务，便于测试和替换。
    编排器只负责流程控制，不包含具体业务逻辑。
    """

    def __init__(
        self,
        ai_service: Any = None,
        playwright_service: Any = None,
        report_service: Any = None,
    ) -> None:
        """依赖注入初始化

        Args:
            ai_service: AIService 实例
            playwright_service: PlaywrightService 实例
            report_service: ReportService 实例
        """
        self.ai_service = ai_service
        self.playwright_service = playwright_service
        self.report_service = report_service

    # ═══════════════════════════════════════════════
    # 完整流水线
    # ═══════════════════════════════════════════════

    async def run_full_pipeline(
        self,
        project_id: int,
        case_ids: list[int],
        mode: str = "headless",
        batch_name: str | None = None,
    ) -> dict:
        """完整流水线：检查 → 生成（如需）→ 执行 → 监听 → 报告

        Returns:
            { "execution_id": 1, "status": "running", "generated": 3 }
        """
        # Step 1: 检查用例是否已生成代码，未生成的先补生成
        cases_to_generate = await self._check_cases_need_generation(case_ids)
        generated_count = 0
        if cases_to_generate:
            logger.info("编排器: %s 个用例尚未生成代码，先批量生成", len(cases_to_generate))
            try:
                results = self.ai_service.generate_batch(project_id, cases_to_generate)
                generated_count = sum(1 for r in results if r.get("status") == "success")
                logger.info("编排器: 代码生成完成 %s/%s", generated_count, len(cases_to_generate))
            except Exception as e:
                # 异常隔离：生成失败不阻塞执行，继续用已有代码
                logger.warning("编排器: 代码生成失败（异常隔离），继续执行: %s", e)

        # Step 2: 创建执行记录 + 异步启动执行
        execution_id = self.playwright_service.create_execution(
            project_id, case_ids, mode, batch_name,
        )

        # 清除旧的停止标志
        from app.services.playwright_service import clear_stop_flag
        clear_stop_flag(execution_id)

        # 后台线程启动执行
        import threading

        def _run():
            from app.db.database import SessionLocal
            db_session = SessionLocal()
            try:
                from app.services.playwright_service import PlaywrightService
                svc = PlaywrightService(db_session)
                svc.execute(project_id, case_ids, execution_id, mode)
            except Exception:
                logger.exception("后台执行线程异常: execution_id=%s", execution_id)
                # 兜底：确保执行状态不为 running
                try:
                    from app.models.execution import Execution
                    from datetime import datetime as dt
                    exec_row = db_session.query(Execution).filter(Execution.id == execution_id).first()
                    if exec_row and exec_row.status == "running":
                        exec_row.status = "failed"
                        exec_row.end_time = dt.utcnow()
                        db_session.commit()
                except Exception:
                    pass
            finally:
                db_session.close()
                clear_stop_flag(execution_id)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Step 3: 启动后台协程监听执行完成 → 生成报告
        asyncio.create_task(self._monitor_and_generate_report(execution_id))

        logger.info("编排器: 流水线已启动 execution_id=%s cases=%s", execution_id, len(case_ids))
        return {
            "execution_id": execution_id,
            "status": "running",
            "generated": generated_count,
        }

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
    ) -> dict:
        """仅执行（假设代码已生成），启动执行 + 监听报告"""
        execution_id = self.playwright_service.create_execution(
            project_id, case_ids, mode, batch_name,
        )

        from app.services.playwright_service import clear_stop_flag
        clear_stop_flag(execution_id)

        import threading

        def _run():
            from app.db.database import SessionLocal
            db_session = SessionLocal()
            try:
                from app.services.playwright_service import PlaywrightService
                svc = PlaywrightService(db_session)
                svc.execute(project_id, case_ids, execution_id, mode)
            except Exception:
                logger.exception("后台执行线程异常: execution_id=%s", execution_id)
                # 兜底：确保执行状态不为 running
                try:
                    from app.models.execution import Execution
                    from datetime import datetime as dt
                    exec_row = db_session.query(Execution).filter(Execution.id == execution_id).first()
                    if exec_row and exec_row.status == "running":
                        exec_row.status = "failed"
                        exec_row.end_time = dt.utcnow()
                        db_session.commit()
                except Exception:
                    pass
            finally:
                db_session.close()
                clear_stop_flag(execution_id)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        asyncio.create_task(self._monitor_and_generate_report(execution_id))

        logger.info("编排器: 仅执行模式启动 execution_id=%s", execution_id)
        return {
            "execution_id": execution_id,
            "status": "running",
        }

    # ═══════════════════════════════════════════════
    # 内部方法 — 状态监听 + 自动生成报告
    # ═══════════════════════════════════════════════

    async def _monitor_and_generate_report(self, execution_id: int) -> None:
        """监听 execution 状态，当变为 'completed' 时生成报告

        每 2 秒轮询一次，最多持续 30 分钟。
        如果状态为 'stopped' 或 'failed'，跳过报告生成。
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
                if status == "completed":
                    logger.info("编排器: execution_id=%s 已完成，自动生成报告", execution_id)
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
                        logger.error("编排器: 报告生成失败 execution_id=%s: %s", execution_id, e)
                    break
                elif status in ("stopped", "failed"):
                    logger.info("编排器: execution_id=%s 状态=%s，跳过报告生成", execution_id, status)
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
