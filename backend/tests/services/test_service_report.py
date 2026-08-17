"""报告服务测试 — pytest 风格，覆盖数据聚合、分类、渲染、文件管理、清理"""

import json
import os
from datetime import datetime as dt
from unittest.mock import patch, MagicMock

import pytest

from app.services.report_service import ReportService
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.report import Report


# ═══════════════════════════════════════════════
# generate()
# ═══════════════════════════════════════════════

class TestGenerate:
    """generate() 报告生成"""

    def test_execution_not_found_raises_value_error(self, db_session):
        svc = ReportService(db_session)
        with pytest.raises(ValueError, match="不存在"):
            svc.generate(99999)

    def test_existing_report_returns_cached(self, db_session, sample_execution, mock_jinja_template, mock_file_ops):
        """已有报告时直接返回缓存，不重新渲染"""
        svc = ReportService(db_session)
        # 第一次生成
        result1 = svc.generate(sample_execution.id)
        assert "report_id" in result1
        assert "download_url" in result1
        call_count_before = mock_jinja_template.render.call_count

        # 第二次生成（应返回缓存）
        result2 = svc.generate(sample_execution.id)
        assert result2["report_id"] == result1["report_id"]
        assert result2["download_url"] == result1["download_url"]
        # render 不应被再次调用
        assert mock_jinja_template.render.call_count == call_count_before

    def test_new_report_generates_html_and_db_record(self, db_session, sample_execution, mock_jinja_template, mock_file_ops):
        """新报告：HTML 生成 + DB 记录创建"""
        svc = ReportService(db_session)
        result = svc.generate(sample_execution.id)

        assert "report_id" in result
        assert "download_url" in result
        assert result["report_id"] > 0
        assert "execution_" in result["download_url"]

        # 验证 DB 中有报告记录
        report = (
            db_session.query(Report)
            .filter(Report.execution_id == sample_execution.id)
            .first()
        )
        assert report is not None
        assert report.report_html is not None
        assert report.download_url is not None


# ═══════════════════════════════════════════════
# _determine_status()
# ═══════════════════════════════════════════════

class TestDetermineStatus:
    """_determine_status() 状态判定"""

    def _make_step(self, status):
        """创建指定状态的 mock ExecutionStep"""
        step = MagicMock(spec=ExecutionStep)
        step.status = status
        return step

    def test_all_success(self):
        steps = [self._make_step("success"), self._make_step("success")]
        assert ReportService._determine_status(steps) == "success"

    def test_one_failed(self):
        steps = [self._make_step("success"), self._make_step("failed")]
        assert ReportService._determine_status(steps) == "failed"

    def test_all_skipped(self):
        steps = [self._make_step("skipped"), self._make_step("skipped")]
        assert ReportService._determine_status(steps) == "skipped"

    def test_mix_success_and_pending(self):
        """success + pending 混合 → success（至少有一步成功）"""
        steps = [self._make_step("success"), self._make_step("pending")]
        assert ReportService._determine_status(steps) == "success"

    def test_all_pending(self):
        steps = [self._make_step("pending"), self._make_step("pending")]
        assert ReportService._determine_status(steps) == "skipped"


# ═══════════════════════════════════════════════
# _analyze_errors()
# ═══════════════════════════════════════════════

class TestAnalyzeErrors:
    """_analyze_errors() 错误分析"""

    def _make_failed_step(self, error_message):
        step = MagicMock(spec=ExecutionStep)
        step.status = "failed"
        step.error_message = error_message
        return step

    def test_correct_error_type_counts(self):
        steps = [
            self._make_failed_step("Timeout 30000ms exceeded"),
            self._make_failed_step("Timeout waiting for selector"),
            self._make_failed_step("Element not found: #btn"),
            self._make_failed_step("Assertion failed: expected visible"),
        ]
        result = ReportService._analyze_errors(steps)

        # 按 count 降序排列
        types = {r["type"]: r["count"] for r in result}
        assert types.get("TimeoutError") == 2
        assert types.get("ElementNotFoundError") == 1
        assert types.get("AssertionError") == 1

    def test_no_errors_returns_empty(self):
        step = MagicMock(spec=ExecutionStep)
        step.status = "success"
        step.error_message = None
        result = ReportService._analyze_errors([step])
        assert result == []


# ═══════════════════════════════════════════════
# _classify_error_type()
# ═══════════════════════════════════════════════

class TestClassifyErrorType:
    """_classify_error_type() 错误类型分类（静态方法）"""

    def test_timeout(self):
        assert ReportService._classify_error_type("Timeout 30000ms exceeded") == "TimeoutError"

    def test_element_not_found(self):
        assert ReportService._classify_error_type("Element not found: #missing") == "ElementNotFoundError"

    def test_element_locator(self):
        assert ReportService._classify_error_type("Cannot resolve locator") == "ElementNotFoundError"

    def test_assertion(self):
        assert ReportService._classify_error_type("Assertion failed: expected visible") == "AssertionError"

    def test_expect(self):
        assert ReportService._classify_error_type("expect(...).to_be_visible failed") == "AssertionError"

    def test_navigation(self):
        assert ReportService._classify_error_type("net::ERR_CONNECTION_REFUSED") == "NavigationError"

    def test_navigation_word(self):
        assert ReportService._classify_error_type("net::ERR_ABORTED") == "NavigationError"

    def test_other(self):
        assert ReportService._classify_error_type("some random error") == "OtherError"


# ═══════════════════════════════════════════════
# _top_failed_selectors()
# ═══════════════════════════════════════════════

class TestTopFailedSelectors:
    """_top_failed_selectors() 失败选择器 TOP N"""

    def _make_failed_step(self, selector):
        step = MagicMock(spec=ExecutionStep)
        step.status = "failed"
        step.target_selector = selector
        return step

    def test_returns_top_n_by_failure_count(self):
        steps = [
            self._make_failed_step("#btn-submit"),
            self._make_failed_step("#btn-submit"),
            self._make_failed_step("#btn-submit"),
            self._make_failed_step("#input-email"),
            self._make_failed_step("#input-email"),
            self._make_failed_step("#link-help"),
        ]
        result = ReportService._top_failed_selectors(steps, limit=3)

        assert len(result) <= 3
        assert result[0]["selector"] == "#btn-submit"
        assert result[0]["count"] == 3
        assert result[1]["selector"] == "#input-email"
        assert result[1]["count"] == 2

    def test_no_failed_steps_returns_empty(self):
        step = MagicMock(spec=ExecutionStep)
        step.status = "success"
        step.target_selector = "#btn"
        result = ReportService._top_failed_selectors([step])
        assert result == []


# ═══════════════════════════════════════════════
# _priority_distribution()
# ═══════════════════════════════════════════════

class TestPriorityDistribution:
    """_priority_distribution() 优先级分布"""

    def test_pass_fail_counts_per_priority(self):
        case_results = [
            {"priority": "P0", "final_status": "success"},
            {"priority": "P0", "final_status": "failed"},
            {"priority": "P1", "final_status": "success"},
            {"priority": "P1", "final_status": "success"},
            {"priority": "P2", "final_status": "failed"},
        ]
        result = ReportService._priority_distribution(case_results)

        assert len(result) == 3  # P0, P1, P2
        priorities = {r["priority"]: r for r in result}
        assert priorities["P0"]["pass"] == 1
        assert priorities["P0"]["fail"] == 1
        assert priorities["P1"]["pass"] == 2
        assert priorities["P1"]["fail"] == 0
        assert priorities["P2"]["pass"] == 0
        assert priorities["P2"]["fail"] == 1


# ═══════════════════════════════════════════════
# _relative_path()
# ═══════════════════════════════════════════════

class TestRelativePath:
    """_relative_path() 路径转换"""

    def test_converts_to_relative(self, monkeypatch, tmp_path):
        """绝对路径转为相对路径，使用 ../ 前缀"""
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        screenshot_file = tmp_path / "uploads" / "screenshots" / "1" / "step_1_before.jpg"
        screenshot_file.parent.mkdir(parents=True)
        screenshot_file.write_text("fake")

        # 设置 REPORT_DIR 为 tmp_path/reports
        monkeypatch.setattr("app.config.settings.REPORT_DIR", str(reports_dir))

        rel = ReportService._relative_path(str(screenshot_file))
        # 相对路径应以 ../ 开头
        assert rel.startswith("../")
        assert "uploads" in rel
        assert "step_1_before.jpg" in rel


# ═══════════════════════════════════════════════
# get_report_info()
# ═══════════════════════════════════════════════

class TestGetReportInfo:
    """get_report_info() 报告信息查询"""

    def test_existing_returns_dict_with_summary(self, db_session, sample_execution, mock_jinja_template, mock_file_ops):
        """已有报告 → 返回 summary 等字段"""
        svc = ReportService(db_session)
        svc.generate(sample_execution.id)

        info = svc.get_report_info(sample_execution.id)
        assert info is not None
        assert "report_id" in info
        assert "execution_id" in info
        assert "summary" in info
        assert "download_url" in info
        assert "created_at" in info
        assert info["execution_id"] == sample_execution.id

    def test_nonexistent_returns_none(self, db_session):
        svc = ReportService(db_session)
        info = svc.get_report_info(99999)
        assert info is None


# ═══════════════════════════════════════════════
# cleanup_old_reports()
# ═══════════════════════════════════════════════

class TestCleanupOldReports:
    """cleanup_old_reports() 过期报告清理"""

    def test_no_reports_dir_returns_zero(self, monkeypatch, tmp_path):
        """报告目录不存在 → 返回 0"""
        nonexistent = str(tmp_path / "nonexistent_reports")
        monkeypatch.setattr("app.config.settings.REPORT_DIR", nonexistent)

        result = ReportService.cleanup_old_reports()
        assert result == 0

    def test_cleans_old_reports(self, monkeypatch, tmp_path):
        """清理超过 max_days 天的报告文件"""
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        monkeypatch.setattr("app.config.settings.REPORT_DIR", str(reports_dir))

        # 创建报告文件
        old_file = reports_dir / "execution_1_report.html"
        old_file.write_text("<html>old</html>")

        new_file = reports_dir / "execution_2_report.html"
        new_file.write_text("<html>new</html>")

        # 设置旧文件时间为 60 天前
        old_time = dt.now().timestamp() - 60 * 86400
        os.utime(str(old_file), (old_time, old_time))

        # 清理 30 天前的
        result = ReportService.cleanup_old_reports(max_days=30)
        assert result == 1

        # 旧文件被删除，新文件保留
        assert not old_file.exists()
        assert new_file.exists()