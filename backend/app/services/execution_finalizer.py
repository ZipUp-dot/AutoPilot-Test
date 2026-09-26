"""ExecutionFinalizer —— Execution 终态收敛（Seal）

唯一负责把 Execution 从任意非终态收敛到终态的地方。completed / stopped / failed /
interrupted 四条路径（含 Recovery 触发、Pre-start Drift Guard）都必须经过 seal()，
禁止绕过本模块直接写 Execution.status（否则半终态泄漏：Execution terminal 而 Case
仍 pending/running）。

收敛顺序（钉死）：
  ① 对【收敛前快照】运行 CaseStateResolver——若结果为 unknown（数据畸形），立即
     记录 integrity_anomaly（这是判定依据，禁止等 Step 改写后再算）。
  ② 执行 Step 改写：running→failed(对应原因)、pending→skipped(对应原因)、
     open HealRound→cancelled_by_recovery。
  ③ 最终 Execution 状态：存在 integrity 异常时固定 failed(integrity_anomaly)，
     且该 Case 的 terminal_reason 保持 integrity_anomaly（不得被改写后的
     failed(incomplete_execution) 覆盖）；否则取请求的终态。

Execution counters（passed_cases / failed_cases / progress）为 Seal 时从 Resolver
重算的 Derived Cache；执行过程不再逐次自增维护。

幂等：Execution 已终态 → seal 直接返回快照，不重复改写。
"""

import json
import logging
from datetime import datetime

from app.utils import terminal_reason as _tr
from app.utils.case_state_resolver import resolve, step_to_dict, _INTEGRITY_MARKER

logger = logging.getLogger("autopilot.finalizer")

# Execution 终态集合（幂等判定）
TERMINAL_STATUSES = frozenset({"completed", "stopped", "failed", "interrupted"})

# context_reason → pending 步骤的 skip_reason（无失败证据的 Case 兜底）
_CONTEXT_SKIP_REASON = {
    _tr.USER_STOPPED: "user_stopped",
    _tr.INTERRUPTED: "interrupted",
    _tr.EXECUTION_FAILED: "execution_failed",
}


class ExecutionFinalizer:
    """终态收敛器（每调用方持有独立 DB 会话）"""

    def __init__(self, db) -> None:
        self._db = db

    # ─────────────────────────────────────────────
    # 公开入口
    # ─────────────────────────────────────────────

    def seal(
        self,
        execution_id: int,
        terminal_status: str,
        context_reason: str,
        *,
        pending_skip_reason: str | None = None,
    ) -> dict:
        """将 execution_id 收敛到终态。

        Args:
            terminal_status: completed / stopped / failed / interrupted（请求的终态；
                存在 integrity 异常时被强制为 failed）。
            context_reason: 六类之一（normal_success / user_stopped / execution_failed /
                interrupted）。
            pending_skip_reason: pending 步骤的 skip_reason 覆盖（如 pre_start_drift）。

        Returns:
            收敛快照 {execution_id, status, passed_cases, failed_cases, progress,
            integrity_anomaly: bool}。已终态时返回当前快照（幂等）。
        """
        from app.models.execution import Execution

        exec_row = self._db.query(Execution).filter(Execution.id == execution_id).first()
        if exec_row is None:
            return {"execution_id": execution_id, "status": "unknown",
                    "passed_cases": 0, "failed_cases": 0, "progress": 0,
                    "integrity_anomaly": False}
        if exec_row.status in TERMINAL_STATUSES:
            return self._snapshot(exec_row)

        steps = (
            self._db.query(exec_step_model())
            .filter(exec_step_model().execution_id == execution_id)
            .order_by(exec_step_model().case_id, exec_step_model().step_index)
            .all()
        )

        # ① 收敛前快照 → Resolver（判定依据，必须先于 Step 改写）
        context = {"reason": context_reason}
        case_steps: dict[int, list] = {}
        integrity_cases: set[int] = set()
        for st in steps:
            case_steps.setdefault(st.case_id, []).append(st)
        for cid, csteps in case_steps.items():
            status, _ = resolve([step_to_dict(s) for s in csteps], context)
            if status == "unknown":
                integrity_cases.add(cid)

        # ② Step 改写
        for cid, csteps in case_steps.items():
            if cid in integrity_cases:
                self._mark_integrity(csteps)
                continue
            has_failed_step = any(s.status == "failed" for s in csteps)
            for s in csteps:
                if s.status == "running":
                    s.status = "failed"
                    s.error_type = "incomplete_execution"  # 中性证据（只推 failed）
                elif s.status == "pending":
                    s.status = "skipped"
                    # 已有真实失败证据的 Case：pending 步骤不写 skip_reason（中性），
                    # 避免收敛改写伪造 execution 证据覆盖真实业务失败；
                    # 无失败证据的 Case：skip_reason 取 context 映射（钉死）。
                    if not has_failed_step:
                        s.skip_reason = pending_skip_reason or _CONTEXT_SKIP_REASON.get(
                            context_reason
                        )
                        # 显式 pending_skip_reason（如 pre_start_drift）同时落 error_type，
                        # 与既有 drift 语义一致（skip_reason + error_type 成对）
                        if pending_skip_reason:
                            s.error_type = pending_skip_reason

        # 开放 HealRound → cancelled_by_recovery
        self._cancel_open_heal_rounds(execution_id)

        # ③ 终态 + counters（Derived Cache）
        actual_status = "failed" if integrity_cases else terminal_status
        passed, failed, total = self._recount(case_steps, context)
        exec_row.status = actual_status
        exec_row.end_time = datetime.utcnow()
        exec_row.passed_cases = passed
        exec_row.failed_cases = failed
        exec_row.progress = round((passed + failed) / total * 100) if total > 0 else 0
        # Terminal Commit Atomicity（P0-10）：runtime_state 终值（case_status +
        # terminal_reason，Resolver 只算这一次）必须与 status 终态 + counters 冻结
        # 在同一 COMMIT 内完成，禁止先 commit terminal status 再异步 Seal。
        self._persist_runtime_state(exec_row, case_steps, context)
        self._db.commit()

        if integrity_cases:
            logger.warning("ExecutionFinalizer: integrity_anomaly execution_id=%s cases=%s",
                           execution_id, sorted(integrity_cases))
        return self._snapshot(exec_row)

    def seal_stopped(
        self,
        execution_id: int,
        *,
        recovered: bool = False,
    ) -> dict:
        """Stop 六步收口事务 —— 全系统 stopped 终态的唯一出口。

        Stop API（queued）与执行器 loop 结束后（stop_requested 为真）与 Recovery
        （stop_requested_at 非 NULL）都只能通过本方法收敛，禁止自行写 status=stopped
        后再补 Seal。

        六步（钉死，缺一不可）：
          ① stop_requested_at=now（Stop 权威字段；已写入则保持原值，幂等）
          ② Execution.status=stopped + end_time
          ③④ 全部非终态 Step → skipped(user_stopped)（有真实失败证据的 Case 的
             pending 步骤不写 skip_reason，避免收敛改写伪造 execution 证据覆盖真实
             业务失败）→ 全部 Case 收敛为 skipped(user_stopped) / 保留成功与真实失败
          ⑤ open HealRound → cancelled_by_recovery
          ⑥ runtime_state 写入最终 case_status / terminal_reason（保留 active_code_id）

        Args:
            recovered: Recovery 触发的 stopped 路径。此时 crash 前在途 case（含
                running 步骤）→ 非终态步骤改为 failed(error_type=recovered_after_stop，
                仅 error_type 层；terminal_reason 仍 user_stopped，不得改判
                interrupted 否则 KPI 误排）；该 case 若已有真实失败证据则保留原语义。

        幂等：Execution 已终态 → 直接返回当前快照。
        """
        from app.models.execution import Execution
        from app.services.execution_state import get_execution_lock

        with get_execution_lock(execution_id):
            exec_row = (
                self._db.query(Execution)
                .filter(Execution.id == execution_id)
                .with_for_update()
                .first()
            )
            if exec_row is None:
                return {"execution_id": execution_id, "status": "unknown",
                        "passed_cases": 0, "failed_cases": 0, "progress": 0,
                        "integrity_anomaly": False}
            if exec_row.status in TERMINAL_STATUSES:
                return self._snapshot(exec_row)

            steps = (
                self._db.query(exec_step_model())
                .filter(exec_step_model().execution_id == execution_id)
                .order_by(exec_step_model().case_id, exec_step_model().step_index)
                .all()
            )
            case_steps: dict[int, list] = {}
            for st in steps:
                case_steps.setdefault(st.case_id, []).append(st)

            # ① stop_requested_at（唯一权威；已写入则幂等保留）
            if exec_row.stop_requested_at is None:
                exec_row.stop_requested_at = datetime.utcnow()
            # ② 终态
            exec_row.status = "stopped"
            exec_row.end_time = datetime.utcnow()

            # ③④ Step 改写（Recovery 路径：在途 case 标记 recovered_after_stop）
            in_flight_cid: int | None = None
            if recovered:
                for cid, csteps in case_steps.items():
                    if any(s.status == "running" for s in csteps):
                        in_flight_cid = cid
                        break
            for cid, csteps in case_steps.items():
                if recovered and cid == in_flight_cid:
                    # 在途 case：非终态步骤 → failed(recovered_after_stop)；
                    # 已存在的真实失败证据（business/execution/integrity）不动，Stop 不得覆盖
                    for s in csteps:
                        if s.status not in ("success", "failed", "skipped"):
                            s.status = "failed"
                            s.error_type = "recovered_after_stop"
                else:
                    has_failed_step = any(s.status == "failed" for s in csteps)
                    for s in csteps:
                        if s.status in ("pending", "running"):
                            s.status = "skipped"
                            if not has_failed_step:
                                s.skip_reason = "user_stopped"

            # ⑤ open HealRound → cancelled_by_recovery
            self._cancel_open_heal_rounds(execution_id)

            # ⑥ runtime_state 写入最终 case_status / terminal_reason
            context = {"reason": _tr.USER_STOPPED}
            self._persist_runtime_state(exec_row, case_steps, context)

            # counters（Derived Cache，Resolver 重算）
            passed, failed, total = self._recount(case_steps, context)
            exec_row.passed_cases = passed
            exec_row.failed_cases = failed
            exec_row.progress = round((passed + failed) / total * 100) if total > 0 else 0
            self._db.commit()

            logger.info("ExecutionFinalizer: stopped 收口 execution_id=%s recovered=%s", execution_id, recovered)
            return self._snapshot(exec_row)

    # ─────────────────────────────────────────────
    # 内部实现
    # ─────────────────────────────────────────────

    def _mark_integrity(self, csteps: list) -> None:
        """完整性异常 Case：所有步骤打 integrity_anomaly 标记（防改写覆盖）"""
        for s in csteps:
            s.error_type = _INTEGRITY_MARKER
            if s.status not in ("success", "failed", "skipped"):
                s.status = "failed"

    def _cancel_open_heal_rounds(self, execution_id: int) -> None:
        from app.models.heal_record import HealRecord
        try:
            self._db.query(HealRecord).filter(
                HealRecord.execution_step_id.in_(
                    self._db.query(exec_step_model().id).filter(
                        exec_step_model().execution_id == execution_id
                    )
                ),
                HealRecord.retry_status.in_(["pending", "retrying", "finalizing"]),
            ).update({"retry_status": "cancelled_by_recovery"})
        except Exception:
            logger.exception("取消开放 HealRound 失败: execution_id=%s", execution_id)

    def _recount(self, case_steps: dict[int, list], context: dict) -> tuple[int, int, int]:
        """Seal 时用 Resolver 重算 counters（Derived Cache）"""
        passed = failed = 0
        total = len(case_steps)
        for csteps in case_steps.values():
            status, _ = resolve([step_to_dict(s) for s in csteps], context)
            if status == "success":
                passed += 1
            elif status == "failed":
                failed += 1
        return passed, failed, total

    def _persist_runtime_state(self, exec_row, case_steps: dict[int, list], context: dict) -> None:
        """⑥ runtime_state 写入最终 case_status / terminal_reason（保留 active_code_id）。

        runtime_state 是唯一持久化真源：终态收敛后逐 case 落最终语义，
        与 Heal Phase B 写入结构一致（{active_code_id, case_status, terminal_reason}）。
        """
        import json as _json
        runtime_state: dict = {}
        if exec_row.runtime_state_json:
            try:
                runtime_state = _json.loads(exec_row.runtime_state_json)
            except (TypeError, ValueError):
                runtime_state = {}
        for cid, csteps in case_steps.items():
            status, reason = resolve([step_to_dict(s) for s in csteps], context)
            entry = dict(runtime_state.get(str(cid), {}))
            entry["case_status"] = status
            entry["terminal_reason"] = reason
            runtime_state[str(cid)] = entry
        exec_row.runtime_state_json = _json.dumps(runtime_state, ensure_ascii=False)

    def _snapshot(self, exec_row) -> dict:
        return {
            "execution_id": exec_row.id,
            "status": exec_row.status,
            "passed_cases": exec_row.passed_cases or 0,
            "failed_cases": exec_row.failed_cases or 0,
            "progress": exec_row.progress or 0,
            "integrity_anomaly": exec_row.status == "failed"
                and self._has_integrity_marker(exec_row.id),
        }

    def _has_integrity_marker(self, execution_id: int) -> bool:
        try:
            row = (
                self._db.query(exec_step_model().error_type)
                .filter(
                    exec_step_model().execution_id == execution_id,
                    exec_step_model().error_type == _INTEGRITY_MARKER,
                )
                .first()
            )
            return row is not None
        except Exception:
            return False


def exec_step_model():
    """延迟导入避免模块加载期循环依赖"""
    from app.models.execution_step import ExecutionStep
    return ExecutionStep
