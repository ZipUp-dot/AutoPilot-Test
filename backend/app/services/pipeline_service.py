"""流水线服务 — 触发（只构造执行请求）/ 防重入 / 派生汇总

PROJ-V20-CICD（F2）。红线（Spec §8 / C-22 教训）：
  - 触发**只构造执行请求**：execution 事实一律由 ExecutionAdmission 创建，
    本模块只调 orchestrator 正式入口并在事后标注归属，禁止第二 Execution Contract；
  - pipeline_run.status 是 Generated/**派生**：唯一 writer = 本模块汇总函数
    （由下属 executions 终态算出），禁止独立状态机（v2.1 §四.4）；
  - webhook token 常量时间比较（hmac.compare_digest），防时序侧信道。

汇总时点（Spec §6 要求挂 execution Seal 后的既有钩子点；§14 白名单未含
execution_finalizer.py，故以「读时 + 触发后重算」实现同一派生语义，
不新增状态出口）。
"""

import hmac
import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from app.exceptions import AppException, NotFoundException, UnauthorizedException
from app.models.execution import Execution
from app.models.pipeline import Pipeline, PipelineRun
from app.utils import terminal_reason as _tr

logger = logging.getLogger("autopilot.pipeline")

# Execution.status 终态（与 ExecutionFinalizer 封存边界一致）
TERMINAL_EXEC_STATUSES = {"completed", "stopped", "failed", "interrupted"}
# 未终态的 run 视为「运行中」（防重入判据）
ACTIVE_RUN_STATUSES = ("queued", "running")

PIPELINE_BATCH_PREFIX = "pipeline:"

# Execution.status → Case 收敛 context（与 executions 路由同一口径）
_EXEC_STATUS_TO_REASON = {
    "completed": _tr.NORMAL_SUCCESS,
    "stopped": _tr.USER_STOPPED,
    "failed": _tr.EXECUTION_FAILED,
    "interrupted": _tr.INTERRUPTED,
}


# ═══════════════════════════════════════════════
# 工具：批次名编码（stage ↔ execution 归属，免加列）
# ═══════════════════════════════════════════════

def build_pipeline_batch_name(pipeline_name: str, run_id: int, stage_name: str) -> str:
    return f"{PIPELINE_BATCH_PREFIX}{pipeline_name}#{run_id}:{stage_name}"


def parse_pipeline_batch_name(batch_name: Optional[str]) -> Optional[dict]:
    if not batch_name or not batch_name.startswith(PIPELINE_BATCH_PREFIX):
        return None
    body = batch_name[len(PIPELINE_BATCH_PREFIX):]
    try:
        head, stage_name = body.split(":", 1)
        pipeline_name, run_id = head.rsplit("#", 1)
        return {
            "pipeline_name": pipeline_name,
            "run_id": int(run_id),
            "stage_name": stage_name,
        }
    except (ValueError, AttributeError):
        return None


def _loads(v: Any, default: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if not v:
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _conflict(message: str) -> AppException:
    return AppException(code=409, message=message, status_code=409)


# ═══════════════════════════════════════════════
# 服务
# ═══════════════════════════════════════════════

class PipelineService:
    """触发 / 防重入 / 派生汇总 / 运行视图"""

    def __init__(self, db: Any = None, orchestrator: Any = None,
                 session_factory: Optional[Callable] = None) -> None:
        self._db = db
        self._orchestrator = orchestrator
        self._session_factory = session_factory

    # ── 会话 / 编排器 ──

    def _open_db(self) -> tuple[Any, bool]:
        if self._db is not None:
            return self._db, False
        if self._session_factory is not None:
            return self._session_factory(), True
        from app.db.database import SessionLocal
        return SessionLocal(), True

    def _get_orchestrator(self, db: Any) -> Any:
        if self._orchestrator is not None:
            return self._orchestrator
        from app.dependencies import get_orchestrator
        return get_orchestrator(db)

    # ── webhook token（常量时间）──

    @staticmethod
    def verify_webhook_token(pipeline: Pipeline, token: Optional[str]) -> None:
        """X-Pipeline-Token 与 pipeline 配置 token 常量时间比对；失败 → 401"""
        cfg = _loads(pipeline.trigger_config_json, {}) or {}
        expected = cfg.get("webhook_token")
        provided = token or ""
        if not expected or not provided or not hmac.compare_digest(str(expected), str(provided)):
            raise UnauthorizedException("pipeline token 无效或缺失")

    # ── 触发 ──

    async def trigger(self, pipeline_id: int, *, trigger_type: str = "manual",
                      trigger_detail: Optional[dict] = None,
                      token: Optional[str] = None,
                      now: Optional[datetime] = None) -> dict:
        """触发一次流水线：建 run → 逐阶段构造执行请求（走 Admission）→ 回填派生状态"""
        now = now or datetime.utcnow()
        db, owns = self._open_db()
        try:
            pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
            if pipeline is None:
                raise NotFoundException(f"流水线 {pipeline_id} 不存在")

            if trigger_type == "webhook":
                self.verify_webhook_token(pipeline, token)

            if not pipeline.enabled:
                raise _conflict("流水线已停用（disabled），禁止触发")

            active = (
                db.query(PipelineRun)
                .filter(PipelineRun.pipeline_id == pipeline_id,
                        PipelineRun.status.in_(ACTIVE_RUN_STATUSES))
                .first()
            )
            if active is not None:
                raise _conflict(f"流水线已有运行中的实例（run #{active.id}），禁止重入")

            run = PipelineRun(
                pipeline_id=pipeline_id,
                trigger_type=trigger_type,
                trigger_detail=(json.dumps(trigger_detail, ensure_ascii=False)
                                if trigger_detail else None),
                status="queued",
                started_at=now,
            )
            db.add(run)
            db.commit()
            db.refresh(run)

            dispatched = await self._dispatch_stages(db, pipeline, run)
            run.status = self.derive_status(run.id)
            db.commit()
            db.refresh(run)
            return {
                "run_id": run.id,
                "pipeline_id": pipeline_id,
                "trigger_type": trigger_type,
                "status": run.status,
                "stages": dispatched,
            }
        finally:
            if owns:
                db.close()

    async def _dispatch_stages(self, db: Any, pipeline: Pipeline,
                               run: PipelineRun) -> list[dict]:
        """逐阶段构造执行请求 → orchestrator 正式入口 → 标注 execution 归属"""
        from app.models.project import Project

        project = db.query(Project).filter(Project.id == pipeline.project_id).first()
        platform = getattr(project, "platform", "web") if project is not None else "web"
        orchestrator = self._get_orchestrator(db)

        dispatched: list[dict] = []
        for stage in (_loads(pipeline.stages_json, []) or []):
            stage_name = stage.get("name") or f"stage{len(dispatched) + 1}"
            selector = stage.get("case_selector") or {}
            case_ids = [int(c) for c in (selector.get("case_ids") or [])]
            execution_id: Optional[int] = None
            try:
                result = await orchestrator.run_execute_only(
                    project_id=pipeline.project_id,
                    case_ids=case_ids,
                    mode="headless",
                    batch_name=build_pipeline_batch_name(pipeline.name, run.id, stage_name),
                    platform=platform,
                )
                if isinstance(result, dict):
                    execution_id = result.get("execution_id")
            except Exception as e:  # noqa: BLE001 — 单阶段失败不阻断其它阶段
                logger.warning("流水线阶段触发失败 run=%s stage=%s: %s",
                               run.id, stage_name, str(e)[:200])

            if execution_id is not None:
                # 只标注归属（execution 事实已由 Admission 创建），不新建事实
                db.query(Execution).filter(Execution.id == execution_id).update(
                    {"pipeline_run_id": run.id}
                )
                db.commit()

            dispatched.append({"name": stage_name, "execution_id": execution_id})
        return dispatched

    # ── 派生汇总（唯一 writer）──

    def derive_status(self, run_id: int) -> str:
        """由下属 executions 终态派生出 run.status（无独立状态机）"""
        db, owns = self._open_db()
        try:
            statuses = [
                s for (s,) in db.query(Execution.status)
                .filter(Execution.pipeline_run_id == run_id)
                .all()
            ]
        finally:
            if owns:
                db.close()

        if not statuses:
            return "queued"
        if all(s == "queued" for s in statuses):
            return "queued"
        if any(s not in TERMINAL_EXEC_STATUSES for s in statuses):
            return "running"
        return "success" if all(s == "completed" for s in statuses) else "failed"

    def refresh_run_status(self, run_id: int, now: Optional[datetime] = None) -> Optional[str]:
        """重算并落库 run.status（汇总任务）；返回最新状态，run 不存在 → None"""
        db, owns = self._open_db()
        try:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if run is None:
                return None
            status = self.derive_status(run_id)
            if run.status != status:
                run.status = status
            if status in ("success", "failed") and run.finished_at is None:
                run.finished_at = now or datetime.utcnow()
            db.commit()
            return run.status
        finally:
            if owns:
                db.close()

    def list_runs(self, pipeline_id: int) -> list[dict]:
        """运行列表（读时刷新派生状态）"""
        db, owns = self._open_db()
        try:
            pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
            if pipeline is None:
                raise NotFoundException(f"流水线 {pipeline_id} 不存在")
            runs = (
                db.query(PipelineRun)
                .filter(PipelineRun.pipeline_id == pipeline_id)
                .order_by(PipelineRun.id.desc())
                .all()
            )
        finally:
            if owns:
                db.close()

        for r in runs:
            self.refresh_run_status(r.id)
        return [self._run_to_dict(r) for r in runs]

    def run_detail(self, run_id: int) -> dict:
        """运行详情：逐阶段 execution 状态 / 通过率（与 executions 接口同口径）"""
        db, owns = self._open_db()
        try:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if run is None:
                raise NotFoundException(f"流水线运行 {run_id} 不存在")
            pipeline = db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()
            executions = (
                db.query(Execution)
                .filter(Execution.pipeline_run_id == run_id)
                .order_by(Execution.id.asc())
                .all()
            )
            by_stage: dict[Optional[str], list[Execution]] = {}
            for e in executions:
                parsed = parse_pipeline_batch_name(e.batch_name)
                by_stage.setdefault(parsed["stage_name"] if parsed else None, []).append(e)

            stages: list[dict] = []
            used: set[int] = set()
            for stage in (_loads(pipeline.stages_json, []) or []) if pipeline else []:
                name = stage.get("name")
                match = by_stage.get(name)
                ex = match[0] if match else None
                if ex is not None:
                    used.add(ex.id)
                stages.append(self._stage_row(db, name, ex))
            # 无法归属到已定义阶段的 execution 也如实列出（不隐藏事实）
            for e in executions:
                if e.id not in used:
                    parsed = parse_pipeline_batch_name(e.batch_name)
                    stages.append(self._stage_row(
                        db, parsed["stage_name"] if parsed else "(unmapped)", e))

            status = self.refresh_run_status(run_id)
            data = self._run_to_dict(run)
            data["status"] = status
            data["stages"] = stages
            return data
        finally:
            if owns:
                db.close()

    # ── 内部 ──

    @staticmethod
    def _run_to_dict(run: PipelineRun) -> dict:
        return {
            "id": run.id,
            "pipeline_id": run.pipeline_id,
            "trigger_type": run.trigger_type,
            "trigger_detail": _loads(run.trigger_detail, None),
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }

    def _stage_row(self, db: Any, name: Optional[str], ex: Optional[Execution]) -> dict:
        row = {"name": name, "execution_id": None, "status": None,
               "passed_cases": 0, "failed_cases": 0, "skipped": 0, "total_cases": 0}
        if ex is None:
            return row
        row["execution_id"] = ex.id
        row["status"] = ex.status
        row.update(self._execution_case_stats(db, ex))
        return row

    @staticmethod
    def _execution_case_stats(db: Any, execution: Execution) -> dict:
        """与 executions 路由同一口径（CaseStateResolver 真值表）"""
        from collections import defaultdict
        from app.models.execution_step import ExecutionStep
        from app.utils.case_state_resolver import resolve, step_to_dict

        steps = (
            db.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == execution.id)
            .all()
        )
        groups: dict[int, list[dict]] = defaultdict(list)
        for s in steps:
            groups[s.case_id].append(step_to_dict(s))

        context = {"reason": _EXEC_STATUS_TO_REASON.get(execution.status)}
        passed = failed = skipped = 0
        for csteps in groups.values():
            status, _ = resolve(csteps, context)
            if status == "success":
                passed += 1
            elif status == "failed":
                failed += 1
            else:
                skipped += 1
        return {
            "passed_cases": passed,
            "failed_cases": failed,
            "skipped": skipped,
            "total_cases": passed + failed + skipped,
        }
