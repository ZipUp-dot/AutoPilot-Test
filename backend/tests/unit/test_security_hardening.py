"""阶段 10 工程收尾测试 — 配置/Excel/报告/文件响应安全

覆盖：
  1. Excel 输入限制：行数 / Sheet 数量 / 单元格长度
  2. 报告 HTML 安全：Jinja2 autoescape 转义 + report_json </script> 转义 + CSP meta
  3. 文件响应安全头：Content-Security-Policy / X-Content-Type-Options
"""

import io
import json
import re
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.utils.excel_parser import (
    ExcelParser,
    MAX_EXCEL_ROWS,
    MAX_EXCEL_SHEETS,
    MAX_CELL_LENGTH,
)


def _make_xlsx(rows: int = 3, sheets: int = 1, evil_cell: str = "") -> bytes:
    """构造内存 xlsx：rows 行（含表头），可指定 Sheet 数与单元格内容"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["用例名称", "操作步骤"])
    for i in range(rows - 1):
        if i == 0 and evil_cell:
            ws.append([evil_cell, "1. click #btn"])
        else:
            ws.append([f"用例{i}", "1. click #btn"])
    for i in range(1, sheets):
        extra = wb.create_sheet(f"Sheet{i + 1}")
        extra.append(["用例名称", "操作步骤"])
        extra.append([f"用例-{i}", "click #x"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestExcelInputLimits:
    """Excel 输入限制（文件大小在 routers/cases.py，此处覆盖行/Sheet/单元格）"""

    def test_row_limit_rejected(self):
        """行数超过 MAX_EXCEL_ROWS → 返回明确错误，不解析"""
        content = _make_xlsx(rows=MAX_EXCEL_ROWS + 1)
        result = ExcelParser.parse(content, "big.xlsx")
        assert result.total_rows == 0
        assert result.failed == 1
        assert any("行数超过限制" in e["reason"] for e in result.errors)

    def test_sheet_limit_rejected(self):
        """Sheet 数量超过 MAX_EXCEL_SHEETS → 返回明确错误"""
        content = _make_xlsx(rows=3, sheets=MAX_EXCEL_SHEETS + 1)
        result = ExcelParser.parse(content, "multi.xlsx")
        assert result.failed == 1
        assert any("Sheet 数量超过限制" in e["reason"] for e in result.errors)

    def test_long_cell_truncated(self):
        """超长单元格截断到 MAX_CELL_LENGTH，防资源耗尽"""
        evil = "A" * (MAX_CELL_LENGTH + 200)
        content = _make_xlsx(rows=2, evil_cell=evil)
        result = ExcelParser.parse(content, "long.xlsx")
        assert result.success >= 1, f"解析失败: {result.errors}"
        case = result.cases[0]
        assert len(case.case_name) == MAX_CELL_LENGTH
        assert case.case_name == "A" * MAX_CELL_LENGTH

    def test_normal_excel_still_works(self):
        """正常 Excel 不受限制影响"""
        content = _make_xlsx(rows=4)
        result = ExcelParser.parse(content, "normal.xlsx")
        assert result.success == 3
        assert result.failed == 0


class TestReportHtmlSafety:
    """报告 HTML 安全：autoescape 转义不可信内容"""

    def _render(self, svc, data: dict) -> str:
        return svc._render(data)

    def _base_data(self) -> dict:
        return {
            "project_name": "测试项目",
            "batch_name": "批次",
            "generated_at": "2026-08-29 00:00:00",
            "execution_mode": "headless",
            "overall_status": "completed",
            "total_cases": 1,
            "passed": 1,
            "failed": 0,
            "skipped": 0,
            "pass_rate": 100,
            "duration": 1,
            "heal_attempts": 0,
            "heal_success": 0,
            "heal_rate": 0,
            "cases": [],
            "error_types": [],
            "top_selectors": [],
            "heal_details": [],
            "gallery": [],
            "report_json": "{}",
        }

    def test_html_in_case_fields_is_escaped(self, db_session):
        """用例名/错误/代码等不可信内容在 HTML 中被转义（autoescape）"""
        from app.services.report_service import ReportService

        svc = ReportService(db_session)
        data = self._base_data()
        data["project_name"] = '<img src=x onerror="alert(1)">'
        data["cases"] = [{
            "case_id": 1,
            "case_name": '<script>alert(1)</script>',
            "priority": "P1",
            "final_status": "failed",
            "duration_ms": 100,
            "step_count": 1,
            "is_healed": False,
            "steps": [{"action": "click", "target": '<b onclick="x">t</b>', "status": "failed", "duration_ms": 10}],
            "screenshots": [],
            "error_summary": '"><script>alert(2)</script>',
            "logs": "log line",
            "code": '<script>alert(3)</script>',
        }]

        html = self._render(svc, data)

        # 项目名与用例名/错误/代码均被 HTML 转义，不存在原始 <script> 标签
        assert "&lt;img src=x" in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
        assert "&lt;script&gt;alert(3)&lt;/script&gt;" in html
        # 页面不应出现可执行的原生 <script> 注入
        assert "<script>alert(1)</script>" not in html
        assert '<img src=x onerror="alert(1)">' not in html

    def test_report_json_script_breakout_prevented(self, db_session):
        """report_json 内嵌 JSON 的 </script> 被转义为 <\\/，防闭合 script 标签"""
        from app.services.report_service import ReportService

        svc = ReportService(db_session)
        data = self._base_data()
        # 模拟真实生成路径：json.dumps(...).replace("</", "<\\/")
        payload = json.dumps({
            "cases": [{"case_name": '</script><script>alert(1)</script>'}],
        }, ensure_ascii=False).replace("</", "<\\/")
        data["report_json"] = payload

        html = self._render(svc, data)
        block = re.search(
            r'<script id="report-data" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        assert block, "报告应包含 report-data 内嵌 JSON 块"
        inner = block.group(1)
        # JSON 块内不允许出现裸 </script>（会被浏览器解析为闭合标签）
        assert "</script>" not in inner.lower()
        # 转义后的序列正确保留
        assert "<\\/script>" in inner

    def test_report_contains_csp_meta(self, db_session):
        """报告 HTML 包含 CSP meta（离线查看同样受保护）"""
        from app.services.report_service import ReportService

        svc = ReportService(db_session)
        html = self._render(svc, self._base_data())
        assert 'http-equiv="Content-Security-Policy"' in html
        assert "default-src 'none'" in html
        assert "frame-ancestors 'none'" in html


class TestFileResponseSecurityHeaders:
    """文件响应安全头（CSP / nosniff）"""

    def test_report_response_has_csp_and_nosniff(self, tmp_path, monkeypatch):
        """受控文件接口返回的报告带 CSP + nosniff 响应头"""
        from app.config import settings
        from app.routers import files

        monkeypatch.setattr(settings, "REPORT_DIR", str(tmp_path))
        report_file = tmp_path / "execution_1_report.html"
        report_file.write_text("<html>test</html>", encoding="utf-8")

        response = files._serve_file(tmp_path, "reports", "execution_1_report.html")
        headers = response.headers  # starlette Headers：大小写不敏感

        assert headers.get("Content-Security-Policy", "").startswith("default-src 'none'")
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert "inline" in headers.get("Content-Disposition", "")
