"""Metrics 服务 — 只读聚合（70% 首生成有效率 / 85% 最终成功率 / Pipeline / Start Coverage）

口径钉死（P0-10）：
  1. 70% 首生成有效率 = SUM(batch_records.summary_json.first_gen_valid_count) /
     SUM(kpi_eligible_count)。first_gen_valid_count / kpi_eligible_count 是 Batch
     Finalization 落库的批级 KPI 专用字段；kpi_eligible 禁止跨指标复用（禁止再出现
     sum(valid) 口径）。
  2. 85% 最终成功率：
     - 分母 = 已成功 Admission 的 production Case = 终态 Execution runtime_state 中
       存在 terminal_reason 的 Case（Seal 时 Resolver 计算一次写死，此处只读）。
     - 排除 terminal_reason ∈ {user_stopped, interrupted}；
       execution_failed / business_failure / integrity_anomaly 保留。
     - 分子 = 分母中 terminal_reason = normal_success 的 Case 数。
     - 分母与 BatchCase.kpi_eligible 无关（kpi_eligible 是 70% 首生成质量专用字段）。
  3. Pipeline Coverage / Execution Start Coverage（规格未给精确公式，自定口径并注释）：
     - Execution Start Coverage = 非 queued 的 Execution 数 / 总 Execution 数
       （queued = Admission 后从未启动；drift 拦截等已 failed 的执行计入 started）。
     - Pipeline Coverage = 终态且有 ready 报告的 Execution 数 / 总 Execution 数
       （完整走完 执行→报告 的流水线占比）。
"""

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("autopilot.metrics")


class MetricsService:
    """只读聚合指标（禁止任何写操作）"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def overview(self, project_id: Optional[int] = None) -> dict:
        """四项 KPI 聚合（project_id 为空 = 全项目口径）"""
        return {
            "first_generation_success_rate": self._first_generation_success_rate(),
            "final_success_rate": self._final_success_rate(project_id),
            "pipeline_coverage": self._pipeline_coverage(project_id),
            "execution_start_coverage": self._execution_start_coverage(project_id),
        }

    def _first_generation_success_rate(self) -> dict:
        """70% 首生成有效率 = SUM(first_gen_valid_count) / SUM(kpi_eligible_count)"""
        from app.models.batch_records import BatchRecord

        total_valid = 0
        total_eligible = 0
        for rec in self._db.query(BatchRecord).all():
            try:
                s = json.loads(rec.summary_json) if rec.summary_json else {}
            except (TypeError, ValueError):
                s = {}
            total_valid += int(s.get("first_gen_valid_count") or 0)
            total_eligible += int(s.get("kpi_eligible_count") or 0)
        rate = (total_valid / total_eligible) if total_eligible > 0 else None
        return {
            "rate": rate,
            "numerator": total_valid,
            "denominator": total_eligible,
        }

    def _final_success_rate(self, project_id: Optional[int]) -> dict:
        """85% 最终成功率（口径钉死见模块 docstring）"""
        from app.models.execution import Execution
        from app.services.execution_finalizer import TERMINAL_STATUSES
        from app.utils import terminal_reason as _tr

        q = self._db.query(Execution)
        if project_id is not None:
            q = q.filter(Execution.project_id == project_id)

        denominator = 0
        numerator = 0
        for ex in q.filter(Execution.status.in_(TERMINAL_STATUSES)).all():
            try:
                runtime = json.loads(ex.runtime_state_json) if ex.runtime_state_json else {}
            except (TypeError, ValueError):
                runtime = {}
            for _cid, entry in runtime.items():
                reason = (entry or {}).get("terminal_reason")
                if not reason:
                    continue  # 未 Seal（非 production Case）：不统计
                if reason in (_tr.USER_STOPPED, _tr.INTERRUPTED):
                    continue  # 排除（KPI 不惩罚人为/环境中断）
                denominator += 1
                if reason == _tr.NORMAL_SUCCESS:
                    numerator += 1
        rate = (numerator / denominator) if denominator > 0 else None
        return {
            "rate": rate,
            "numerator": numerator,
            "denominator": denominator,
        }

    def _execution_start_coverage(self, project_id: Optional[int]) -> dict:
        """Execution Start Coverage = 非 queued 的 Execution / 总 Execution"""
        from app.models.execution import Execution

        q = self._db.query(Execution)
        if project_id is not None:
            q = q.filter(Execution.project_id == project_id)
        total = q.count()
        started = q.filter(Execution.status != "queued").count()
        return {
            "coverage": (started / total) if total > 0 else None,
            "started": started,
            "total": total,
        }

    def _pipeline_coverage(self, project_id: Optional[int]) -> dict:
        """Pipeline Coverage = 终态且已有 ready 报告的 Execution / 总 Execution"""
        from app.models.execution import Execution
        from app.models.report import Report
        from app.services.execution_finalizer import TERMINAL_STATUSES

        q = self._db.query(Execution)
        if project_id is not None:
            q = q.filter(Execution.project_id == project_id)
        total = q.count()
        terminal_rows = q.filter(Execution.status.in_(TERMINAL_STATUSES)).all()
        terminal_ids = [ex.id for ex in terminal_rows]
        ready = 0
        if terminal_ids:
            # 同一 Execution 只生成一个 report_type（终态唯一映射），ready 行数即流水线走完数
            ready = self._db.query(Report).filter(
                Report.execution_id.in_(terminal_ids),
                Report.generation_status == "ready",
            ).count()
        return {
            "coverage": (ready / total) if total > 0 else None,
            "ready": ready,
            "total": total,
        }
