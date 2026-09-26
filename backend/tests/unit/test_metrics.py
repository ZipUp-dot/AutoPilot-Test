"""Metrics 只读聚合测试（P0-10 验收 4）— 70% / 85% / Pipeline / Start Coverage

覆盖验收 4：
  70% 首生成有效率：仅聚合 batch_records.summary_json 的
    first_gen_valid_count / kpi_eligible_count（禁止 sum(valid) 口径）。
  85% 最终成功率：分母 = 终态 Execution runtime_state 中有 terminal_reason 的 Case，
    排除 user_stopped/interrupted；execution_failed/business_failure/integrity_anomaly
    保留；分子 = normal_success。与 BatchCase.kpi_eligible 无关。
  Pipeline Coverage / Execution Start Coverage（自定口径见 metrics_service docstring）。
"""

import json
from datetime import datetime

import pytest

from app.models.batch_records import BatchRecord
from app.models.execution import Execution
from app.models.report import Report
from app.services.metrics_service import MetricsService
from app.utils import terminal_reason as _tr


def _seed_runtime_execution(db, project_id, status, cases):
    """构造终态 Execution。cases = [(case_id, case_status, terminal_reason)]"""
    runtime = {
        str(cid): {"active_code_id": 1, "case_status": cs, "terminal_reason": reason}
        for cid, cs, reason in cases
    }
    ex = Execution(
        project_id=project_id,
        total_cases=len(cases),
        status=status,
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow(),
        runtime_state_json=json.dumps(runtime),
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


class TestFirstGenerationSuccessRate:
    """70% 首生成有效率（batch_records 聚合）"""

    def test_aggregates_batch_records_summary(self, db_session, sample_project):
        db_session.add(BatchRecord(
            project_id=sample_project.id, batch_id="b1", batch_status="completed",
            summary_json=json.dumps({"first_gen_valid_count": 7, "kpi_eligible_count": 10}),
        ))
        db_session.add(BatchRecord(
            project_id=sample_project.id, batch_id="b2", batch_status="completed",
            summary_json=json.dumps({"first_gen_valid_count": 3, "kpi_eligible_count": 5}),
        ))
        db_session.commit()

        result = MetricsService(db_session)._first_generation_success_rate()
        assert result["numerator"] == 10
        assert result["denominator"] == 15
        assert result["rate"] == pytest.approx(10 / 15)

    def test_ignores_generated_codes_valid_flag(self, db_session, sample_project,
                                                sample_test_case, sample_generated_code):
        """禁止 sum(valid) 口径：generated_codes.is_valid=1 不计入 70% 分母/分子"""
        result = MetricsService(db_session)._first_generation_success_rate()
        assert result["denominator"] == 0
        assert result["numerator"] == 0
        assert result["rate"] is None

    def test_empty_returns_none(self, db_session):
        result = MetricsService(db_session)._first_generation_success_rate()
        assert result["rate"] is None
        assert result["denominator"] == 0

    def test_kpi_eligible_denominator_zero_returns_none(self, db_session, sample_project):
        """分母为 0（eligible=0）→ rate=None（避免除零）"""
        db_session.add(BatchRecord(
            project_id=sample_project.id, batch_id="b1", batch_status="completed",
            summary_json=json.dumps({"first_gen_valid_count": 0, "kpi_eligible_count": 0}),
        ))
        db_session.commit()
        result = MetricsService(db_session)._first_generation_success_rate()
        assert result["rate"] is None
        assert result["denominator"] == 0


class TestFinalSuccessRate:
    """85% 最终成功率（terminal_reason 过滤，与 kpi_eligible 无关）"""

    def test_denominator_excludes_user_stopped_and_interrupted(
        self, db_session, sample_project,
    ):
        """验收 4：user_stopped（排除）+ execution_failed（保留）+ 其他混合数据"""
        _seed_runtime_execution(db_session, sample_project.id, "completed", [
            (1, "success", _tr.NORMAL_SUCCESS),          # 分子 + 分母
            (2, "failed", _tr.EXECUTION_FAILED),        # 分母（保留）
            (3, "failed", _tr.BUSINESS_FAILURE),        # 分母（保留）
            (4, "skipped", _tr.USER_STOPPED),           # 排除
        ])
        _seed_runtime_execution(db_session, sample_project.id, "interrupted", [
            (5, "failed", _tr.INTERRUPTED),             # 排除
        ])
        _seed_runtime_execution(db_session, sample_project.id, "failed", [
            (6, "failed", _tr.INTEGRITY_ANOMALY),       # 分母（保留）
        ])

        result = MetricsService(db_session)._final_success_rate(sample_project.id)
        assert result["denominator"] == 4   # case 1,2,3,6
        assert result["numerator"] == 1     # case 1
        assert result["rate"] == pytest.approx(1 / 4)

    def test_skips_entries_without_terminal_reason(self, db_session, sample_project):
        """未 Seal 的 Case（runtime_state 无 terminal_reason）不统计（非 production Case）"""
        ex = Execution(
            project_id=sample_project.id, status="completed",
            runtime_state_json=json.dumps({"7": {"active_code_id": 3}}),
        )
        db_session.add(ex)
        db_session.commit()
        result = MetricsService(db_session)._final_success_rate(sample_project.id)
        assert result["denominator"] == 0
        assert result["numerator"] == 0

    def test_non_terminal_executions_excluded(self, db_session, sample_project):
        """仅终态 Execution 参与 85%（queued/running/healing 不计）"""
        _seed_runtime_execution(db_session, sample_project.id, "running", [
            (1, "success", _tr.NORMAL_SUCCESS),
        ])
        result = MetricsService(db_session)._final_success_rate(sample_project.id)
        assert result["denominator"] == 0

    def test_kpi_eligible_does_not_leak_into_85(self, db_session, sample_project):
        """kpi_eligible 是 70% 专用：无 BatchRecord 时 85% 仍正常计算"""
        _seed_runtime_execution(db_session, sample_project.id, "completed", [
            (1, "success", _tr.NORMAL_SUCCESS),
        ])
        result = MetricsService(db_session)._final_success_rate(sample_project.id)
        assert result["denominator"] == 1
        assert result["numerator"] == 1

    def test_project_filter(self, db_session, sample_project):
        """project_id 过滤：只统计该项目的 Case"""
        _seed_runtime_execution(db_session, sample_project.id, "completed", [
            (1, "success", _tr.NORMAL_SUCCESS),
        ])
        other = Execution(
            project_id=9999, status="completed",
            runtime_state_json=json.dumps({"1": {
                "active_code_id": 1, "case_status": "success",
                "terminal_reason": _tr.NORMAL_SUCCESS,
            }}),
        )
        db_session.add(other)
        db_session.commit()
        result = MetricsService(db_session)._final_success_rate(sample_project.id)
        assert result["denominator"] == 1
        assert result["numerator"] == 1


class TestCoverage:
    """Pipeline Coverage / Execution Start Coverage"""

    def test_execution_start_coverage(self, db_session, sample_project):
        from datetime import datetime
        for status in ("queued", "running", "completed", "failed"):
            db_session.add(Execution(
                project_id=sample_project.id, status=status,
                start_time=datetime.utcnow(),
            ))
        db_session.commit()

        result = MetricsService(db_session)._execution_start_coverage(sample_project.id)
        assert result["total"] == 4
        assert result["started"] == 3  # queued 未启动
        assert result["coverage"] == pytest.approx(3 / 4)

    def test_pipeline_coverage(self, db_session, sample_project):
        ex1 = _seed_runtime_execution(db_session, sample_project.id, "completed", [])
        ex2 = _seed_runtime_execution(db_session, sample_project.id, "completed", [])
        ex3 = _seed_runtime_execution(db_session, sample_project.id, "queued", [])
        # ex1 有 ready 报告 → pipeline 走完；ex2/ex3 无
        db_session.add(Report(
            execution_id=ex1.id, report_type="full",
            generation_status="ready",
        ))
        db_session.commit()

        result = MetricsService(db_session)._pipeline_coverage(sample_project.id)
        assert result["total"] == 3
        assert result["ready"] == 1
        assert result["coverage"] == pytest.approx(1 / 3)


class TestOverview:
    def test_overview_returns_four_metrics(self, db_session, sample_project):
        _seed_runtime_execution(db_session, sample_project.id, "completed", [
            (1, "success", _tr.NORMAL_SUCCESS),
        ])
        data = MetricsService(db_session).overview(sample_project.id)
        assert set(data.keys()) == {
            "first_generation_success_rate",
            "final_success_rate",
            "pipeline_coverage",
            "execution_start_coverage",
        }
        assert data["final_success_rate"]["denominator"] == 1
