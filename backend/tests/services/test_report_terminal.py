"""Report 终态分型 + 幂等/fencing 测试（P0-10 验收 2/3/5）

覆盖验收：
  ② 四种终态各生成对应 report_type（completed→full、stopped→partial、
     failed→diagnostic、interrupted→interrupted）；同一 Execution 只生成与其
     终态对应的那一个 report_type。
  ③ Report 中 URL/名称与 Manifest 一致（Admission 时冻结 project 快照；
     改 Project 后旧报告不变）。
  ⑤ Report Claim Fencing 竞态：A claim(token=A) → A 超时 → B reclaim(token=B)
     → B ready → A 用旧 token=A 写回 → UPDATE 影响行数=0、B 的结果不被覆盖。
  附：claim 状态机（generating 未超时拒绝 / failed 可重新 claim）。
"""

import json
from datetime import datetime

import pytest

from app.models.execution import Execution
from app.models.report import Report
from app.services.report_service import (
    ReportClaimConflict,
    ReportService,
    report_type_for_status,
)


def _make_terminal_execution(db, project_id, status, manifest_project=None):
    ex = Execution(
        project_id=project_id,
        total_cases=0,
        status=status,
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow(),
        manifest_json=json.dumps(
            {"schema_version": 1, "project": manifest_project or {
                "name": "快照项目", "target_url": "https://snapshot.example.com",
            }}
        ),
        runtime_state_json=json.dumps({}),
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


def _summary(**kw):
    return {
        "total_cases": 0, "passed": 0, "failed": 0, "skipped": 0,
        "pass_rate": 0, "duration": 0, "heal_attempts": 0, "heal_success": 0,
        **kw,
    }


class TestReportTypeMapping:
    """验收 ②：终态分型映射钉死（report_type 值域仅 4 类 + 非终态兜底 full）"""

    def test_mapping_pinned(self):
        assert report_type_for_status("completed") == "full"
        assert report_type_for_status("stopped") == "partial"
        assert report_type_for_status("failed") == "diagnostic"
        assert report_type_for_status("interrupted") == "interrupted"

    def test_non_terminal_falls_back_to_full(self):
        for status in ("queued", "running", "healing"):
            assert report_type_for_status(status) == "full"

    @pytest.mark.parametrize("status,expected_type", [
        ("completed", "full"),
        ("stopped", "partial"),
        ("failed", "diagnostic"),
        ("interrupted", "interrupted"),
    ])
    def test_terminal_status_generates_matching_report(
        self, db_session, sample_project, status, expected_type,
        mock_jinja_template, mock_file_ops,
    ):
        """四终态各生成对应类型报告（含 interrupted）"""
        ex = _make_terminal_execution(db_session, sample_project.id, status)
        svc = ReportService(db_session)
        result = svc.generate(ex.id)

        report = (
            db_session.query(Report)
            .filter(
                Report.execution_id == ex.id,
                Report.report_type == expected_type,
            )
            .first()
        )
        assert report is not None
        assert report.generation_status == "ready"
        assert "report_" in result["download_url"]

    def test_single_execution_only_one_report_type(
        self, db_session, sample_project, mock_jinja_template, mock_file_ops,
    ):
        """同一 Execution 只生成与其终态对应的那一个 report_type"""
        ex = _make_terminal_execution(db_session, sample_project.id, "stopped")
        svc = ReportService(db_session)
        svc.generate(ex.id)

        rows = (
            db_session.query(Report)
            .filter(Report.execution_id == ex.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].report_type == "partial"

        # 再次生成 → 幂等复用同一行（ready）
        svc.generate(ex.id)
        assert (
            db_session.query(Report)
            .filter(Report.execution_id == ex.id)
            .count()
        ) == 1


class TestReportManifestSnapshot:
    """验收 ③：报告 URL/名称读 Manifest 快照，改 Project 后旧报告不变"""

    def test_report_uses_manifest_snapshot_not_current_project(
        self, db_session, sample_project,
    ):
        ex = _make_terminal_execution(
            db_session, sample_project.id, "completed",
            manifest_project={"name": "旧名称", "target_url": "https://old.example.com"},
        )
        # 修改 Project 当前值（Seal 后改项目，旧报告必须不变）
        sample_project.name = "新名称"
        sample_project.target_url = "https://new.example.com"
        db_session.commit()

        svc = ReportService(db_session)
        html, data = svc._render_report(ex.id)

        assert data["project_name"] == "旧名称"
        assert data["target_url"] == "https://old.example.com"
        assert "旧名称" in html
        assert "https://old.example.com" in html
        assert "新名称" not in html


class TestReportClaimFencing:
    """验收 ⑤：fencing 竞态——旧 owner 晚到写回不得覆盖新 owner 结果"""

    def test_stale_token_writeback_does_not_override(
        self, db_session, sample_project, mock_settings,
    ):
        """A claim(token=A) → A 卡死 → B reclaim(token=B) → B ready →
        A 用旧 token=A 写回 → 影响行数=0、B 的结果不被覆盖"""
        ex = _make_terminal_execution(db_session, sample_project.id, "completed")
        svc = ReportService(db_session)

        # A claim（token=A）
        report, token_a = svc._claim(ex, "full")
        assert report.generation_status == "generating"
        assert token_a == report.claim_token

        # A 卡死（未写回）。超时阈值置 0 → B reclaim 生成新 token=B，旧 token 失效
        mock_settings("REPORT_CLAIM_TIMEOUT_SECONDS", 0)
        report_b, token_b = svc._claim(ex, "full")
        assert token_b != token_a
        assert report_b.claim_token == token_b
        assert report_b.generation_status == "generating"

        # B 生成完成 → fenced 写回 ready
        b_url = f"/reports/report_{ex.id}_full_{token_b}.html"
        b = svc._mark_ready(
            ex, "full", token_b, "<html>B</html>", _summary(), b_url
        )
        assert b is not None

        # A 用旧 token=A 晚到写回 → fenced UPDATE 影响行数=0 → 返回 None（不覆盖）
        a_url = f"/reports/report_{ex.id}_full_{token_a}.html"
        a_result = svc._mark_ready(
            ex, "full", token_a, "<html>A</html>", _summary(), a_url
        )
        assert a_result is None

        # DB 结果仍是 B 的（未被 A 覆盖）
        row = (
            db_session.query(Report)
            .filter(Report.execution_id == ex.id, Report.report_type == "full")
            .first()
        )
        assert row.generation_status == "ready"
        assert row.download_url == b_url
        assert "<html>B</html>" in (row.report_html or "")

    def test_stale_token_failed_writeback_does_not_override(
        self, db_session, sample_project, mock_settings,
    ):
        """旧 owner 的失败写回同样受 fencing 保护：B ready 后 A 置 failed 无效"""
        ex = _make_terminal_execution(db_session, sample_project.id, "completed")
        svc = ReportService(db_session)

        _, token_a = svc._claim(ex, "full")
        mock_settings("REPORT_CLAIM_TIMEOUT_SECONDS", 0)
        _, token_b = svc._claim(ex, "full")
        svc._mark_ready(ex, "full", token_b, "<html>B</html>", _summary(),
                        f"/reports/report_{ex.id}_full_{token_b}.html")

        svc._mark_failed(ex, "full", token_a)  # 旧 token 晚到置 failed

        row = (
            db_session.query(Report)
            .filter(Report.execution_id == ex.id, Report.report_type == "full")
            .first()
        )
        assert row.generation_status == "ready"  # B 的结果未被 A 破坏

    def test_generating_not_timed_out_conflicts(self, db_session, sample_project):
        """generating 未超时 → 第二次 claim 拒绝（ReportClaimConflict）"""
        ex = _make_terminal_execution(db_session, sample_project.id, "completed")
        svc = ReportService(db_session)
        svc._claim(ex, "full")  # 默认超时 300s，未超时

        with pytest.raises(ReportClaimConflict):
            svc._claim(ex, "full")

    def test_failed_can_reclaim(self, db_session, sample_project):
        """failed → 下一次独立触发可重新 claim（generating + 新 token）"""
        ex = _make_terminal_execution(db_session, sample_project.id, "completed")
        svc = ReportService(db_session)

        _, token_a = svc._claim(ex, "full")
        svc._mark_failed(ex, "full", token_a)

        report, token_b = svc._claim(ex, "full")
        assert report.generation_status == "generating"
        assert token_b != token_a
        assert report.claim_token == token_b
