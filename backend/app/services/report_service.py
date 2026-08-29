"""测试报告服务 — 数据聚合 + Jinja2 渲染 + 文件管理 + 过期清理

数据源:
  - executions 表：批次信息、总耗时、执行模式
  - execution_steps 表：每步的执行状态、截图、日志、错误
  - test_cases 表：用例名称、优先级、预期结果
  - generated_codes 表：生成的代码（含 healed 版本）
  - heal_records 表：自愈记录

报告输出:
  - HTML 文件：reports/execution_{execution_id}_report.html（内联所有资源，离线可查看）
  - DB 记录：execution_reports 表
"""

import json
import logging
import os
import re
import shutil
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from sqlalchemy.orm import Session

from app.config import settings
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.models.heal_record import HealRecord
from app.models.project import Project
from app.models.report import Report
from app.models.test_case import TestCase

logger = logging.getLogger("autopilot.report")

# 报告生成进程内互斥锁：编排器后台自动生成与手动 POST /reports/generate 并发时，
# 防止同一 execution_id 同时走"查询-插入"导致 execution_reports UNIQUE 冲突。
_REPORT_LOCK = threading.Lock()

# ── Jinja2 环境 ──
# autoescape 开启：模板中所有 {{ }} 输出的不可信动态内容（用例名/错误/日志/代码等）
# 默认 HTML 转义，防止 Excel 内容 / 执行日志 / AI 输出注入 HTML 造成 XSS。
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "htm"]),
)


class ReportService:
    """报告生成服务"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 报告生成
    # ═══════════════════════════════════════════════

    def generate(self, execution_id: int) -> dict:
        """生成 HTML 报告（进程内互斥，防并发重复创建 UNIQUE 冲突）

        Returns:
            { "report_id": 1, "download_url": "..." }
        """
        with _REPORT_LOCK:
            return self._generate(execution_id)

    def _generate(self, execution_id: int) -> dict:
        """生成 HTML 报告（锁内执行）"""
        t0 = time.time()

        # 1. 检查是否已有报告
        existing = (
            self._db.query(Report)
            .filter(Report.execution_id == execution_id)
            .first()
        )
        if existing and existing.report_html:
            logger.info("报告已存在: execution_id=%s", execution_id)
            return {
                "report_id": existing.id,
                "download_url": existing.download_url,
            }

        # 2. 查询执行批次
        execution = (
            self._db.query(Execution)
            .filter(Execution.id == execution_id)
            .first()
        )
        if not execution:
            raise ValueError(f"执行批次 {execution_id} 不存在")

        project = (
            self._db.query(Project)
            .filter(Project.id == execution.project_id)
            .first()
        )

        # 3. 查询所有执行步骤
        steps = (
            self._db.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == execution_id)
            .order_by(ExecutionStep.case_id, ExecutionStep.step_index)
            .all()
        )

        # 4. 查询关联的用例
        case_ids = list(set(s.case_id for s in steps))
        cases_map = {
            c.id: c
            for c in self._db.query(TestCase)
            .filter(TestCase.id.in_(case_ids))
            .all()
        } if case_ids else {}

        # 5. 查询自愈记录
        step_ids = [s.id for s in steps]
        heal_records = {}
        if step_ids:
            for hr in (
                self._db.query(HealRecord)
                .filter(HealRecord.execution_step_id.in_(step_ids))
                .all()
            ):
                heal_records.setdefault(hr.execution_step_id, []).append(hr)

        # 6. 查询生成代码
        gen_codes = {}
        if case_ids:
            for gc in (
                self._db.query(GeneratedCode)
                .filter(GeneratedCode.case_id.in_(case_ids))
                .order_by(GeneratedCode.created_at.desc())
                .all()
            ):
                if gc.case_id not in gen_codes:
                    gen_codes[gc.case_id] = gc

        # 7. 聚合数据
        report_data = self._aggregate(
            execution, project, steps, cases_map, heal_records, gen_codes
        )

        # 8. 渲染 HTML
        html = self._render(report_data)

        # 9. 保存 HTML 文件
        file_path = self._save_html_file(execution_id, html)

        # 10. 保存/更新 DB 记录
        summary = {
            "total_cases": report_data["total_cases"],
            "passed": report_data["passed"],
            "failed": report_data["failed"],
            "skipped": report_data["skipped"],
            "pass_rate": report_data["pass_rate"],
            "duration": report_data["duration"],
            "heal_attempts": report_data["heal_attempts"],
            "heal_success": report_data["heal_success"],
        }
        report = self._save_db(execution_id, html, summary, file_path)

        elapsed = time.time() - t0
        logger.info(
            "报告生成完成: execution_id=%s elapsed=%.2fs size=%s KB",
            execution_id, elapsed, len(html) // 1024,
        )
        return {
            "report_id": report.id,
            "download_url": file_path,
        }

    # ═══════════════════════════════════════════════
    # 数据聚合
    # ═══════════════════════════════════════════════

    def _aggregate(
        self,
        execution: Execution,
        project: Optional[Project],
        steps: list[ExecutionStep],
        cases_map: dict[int, TestCase],
        heal_records: dict[int, list[HealRecord]],
        gen_codes: dict[int, GeneratedCode],
    ) -> dict:
        """聚合所有数据为报告数据结构"""

        # ── 概览统计 ──
        case_steps = defaultdict(list)
        for s in steps:
            case_steps[s.case_id].append(s)

        case_results = []
        passed = failed = skipped = 0
        total_duration_ms = 0

        for case_id, case_steps_list in case_steps.items():
            case = cases_map.get(case_id)
            if not case:
                continue

            final_status = self._determine_status(case_steps_list)
            case_duration = sum(s.duration_ms or 0 for s in case_steps_list)
            total_duration_ms += case_duration

            if final_status == "success":
                passed += 1
            elif final_status == "failed":
                failed += 1
            else:
                skipped += 1

            # 该用例的截图
            screenshots = []
            for s in case_steps_list:
                if s.screenshot_before:
                    screenshots.append({
                        "step_index": s.step_index,
                        "label": "Before",
                        "path": self._relative_path(s.screenshot_before),
                    })
                if s.screenshot_after:
                    screenshots.append({
                        "step_index": s.step_index,
                        "label": "After",
                        "path": self._relative_path(s.screenshot_after),
                    })

            # 日志
            logs = "\n".join(
                s.log_output for s in case_steps_list if s.log_output
            )

            # 错误摘要
            error_summary = ""
            for s in case_steps_list:
                if s.error_message:
                    error_summary = s.error_message[:300]
                    break

            # 代码
            gc = gen_codes.get(case_id)
            code = gc.code_content if gc else ""

            # 自愈信息
            is_healed = False
            healed_code = ""
            original_code = code
            for case_step in case_steps_list:
                if case_step.id in heal_records:
                    for hr in heal_records[case_step.id]:
                        if hr.retry_status == "success" and hr.healed_code:
                            is_healed = True
                            healed_code = hr.healed_code
                            break

            case_results.append({
                "case_id": case_id,
                "case_name": case.case_name,
                "priority": case.priority or "P1",
                "final_status": final_status,
                "is_healed": is_healed,
                "duration_ms": case_duration,
                "step_count": len(case_steps_list),
                "total_duration_ms": case_duration,
                "steps": [
                    {
                        "action": s.action or "",
                        "target": (s.target_selector or "")[:80],
                        "status": s.status or "pending",
                        "duration_ms": s.duration_ms or 0,
                    }
                    for s in case_steps_list
                ],
                "screenshots": screenshots,
                "logs": logs,
                "error_summary": error_summary,
                "code": code,
                "original_code": original_code,
                "healed_code": healed_code,
            })

        total_cases = len(case_results)
        pass_rate = round(passed / total_cases * 100, 1) if total_cases > 0 else 0

        # ── 自愈统计 ──
        heal_attempts = 0
        heal_success = 0
        heal_details_list = []
        for sid, records in heal_records.items():
            for hr in records:
                heal_attempts += 1
                if hr.retry_status == "success":
                    heal_success += 1
                # 找到对应步骤和用例
                step_obj = next((s for s in steps if s.id == sid), None)
                if step_obj:
                    case_obj = cases_map.get(step_obj.case_id)
                    heal_details_list.append({
                        "step_index": step_obj.step_index,
                        "case_name": case_obj.case_name if case_obj else "",
                        "retry_count": hr.retry_count,
                        "status": hr.retry_status or "unknown",
                    })

        heal_rate = (
            round(heal_success / heal_attempts * 100, 1)
            if heal_attempts > 0 else 0
        )

        # ── 错误分析 ──
        error_types = self._analyze_errors(steps)
        top_selectors = self._top_failed_selectors(steps)

        # ── 优先级分布 ──
        priority_dist = self._priority_distribution(case_results)

        # ── 截图画廊 ──
        gallery = []
        for s in steps:
            if s.screenshot_before:
                gallery.append({
                    "path": self._relative_path(s.screenshot_before),
                    "label": f"Case#{s.case_id} Step{s.step_index} Before",
                })
            if s.screenshot_after:
                gallery.append({
                    "path": self._relative_path(s.screenshot_after),
                    "label": f"Case#{s.case_id} Step{s.step_index} After",
                })

        # ── 耗时 ──
        duration = 0
        if execution.start_time and execution.end_time:
            duration = round(
                (execution.end_time - execution.start_time).total_seconds(), 1
            )

        return {
            # 模板变量
            "project_name": project.name if project else "",
            "batch_name": execution.batch_name or f"Execution #{execution.id}",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "execution_mode": execution.execution_mode or "headless",
            "overall_status": execution.status or "unknown",
            "total_cases": total_cases,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": pass_rate,
            "duration": duration,
            "heal_attempts": heal_attempts,
            "heal_success": heal_success,
            "heal_rate": heal_rate,
            "cases": case_results,
            "error_types": error_types,
            "top_selectors": top_selectors,
            "heal_details": heal_details_list,
            "gallery": gallery,
            # 内嵌 JSON 供图表 JS 读取
            # 转义 "</" -> "<\/"：防止不可信内容（用例名/错误信息）闭合 <script> 标签逃逸 XSS
            "report_json": json.dumps({
                "overview": {
                    "total_cases": total_cases,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "pass_rate": pass_rate,
                },
                "priority_distribution": priority_dist,
                "error_types": [
                    {"type": e["type"], "count": e["count"]} for e in error_types
                ],
                "cases": [
                    {
                        "case_id": c["case_id"],
                        "case_name": c["case_name"],
                        "priority": c["priority"],
                        "final_status": c["final_status"],
                        "total_duration_ms": c["duration_ms"],
                        "step_count": c["step_count"],
                        "error_summary": c.get("error_summary", ""),
                    }
                    for c in case_results
                ],
            }, ensure_ascii=False).replace("</", "<\\/"),
        }

    # ═══════════════════════════════════════════════
    # 分析辅助方法
    # ═══════════════════════════════════════════════

    @staticmethod
    def _determine_status(case_steps: list[ExecutionStep]) -> str:
        """根据步骤判断用例最终状态

        - 任一 failed → failed
        - 全部 success / skipped / pending 且至少一个 success → success
        - 全部 skipped / pending → skipped
        """
        statuses = [s.status for s in case_steps]
        if "failed" in statuses:
            return "failed"
        if all(s in ("success", "skipped", "pending") for s in statuses):
            return "success" if any(s == "success" for s in statuses) else "skipped"
        return "skipped"

    @staticmethod
    def _analyze_errors(steps: list[ExecutionStep]) -> list[dict]:
        """分析错误类型分布"""
        type_counter: dict[str, int] = {}
        for s in steps:
            if s.status != "failed" or not s.error_message:
                continue
            error_type = ReportService._classify_error_type(s.error_message)
            type_counter[error_type] = type_counter.get(error_type, 0) + 1
        return [
            {"type": k, "count": v}
            for k, v in sorted(type_counter.items(), key=lambda x: -x[1])
        ]

    @staticmethod
    def _classify_error_type(msg: str) -> str:
        """分类错误类型（支持 Web 和 Android 异常）"""
        m = msg.lower()
        # Appium 异常（优先匹配）
        if "staleelementreferenceexception" in m or "stale element" in m:
            return "StaleElementError"
        if "nosuchelementexception" in m:
            return "ElementNotFoundError"
        if "timeoutexception" in m:
            return "TimeoutError"
        if "webdriverexception" in m:
            return "DriverError"
        # Web 异常
        if "timeout" in m:
            return "TimeoutError"
        if "resolve" in m or "locator" in m or "element" in m:
            return "ElementNotFoundError"
        if "assert" in m or "expect" in m:
            return "AssertionError"
        if "navigation" in m or "net::" in m:
            return "NavigationError"
        return "OtherError"

    @staticmethod
    def _top_failed_selectors(steps: list[ExecutionStep], limit: int = 5) -> list[dict]:
        """最常见失败选择器 TOP N"""
        counter: dict[str, int] = {}
        for s in steps:
            if s.status == "failed" and s.target_selector:
                sel = s.target_selector[:100]
                counter[sel] = counter.get(sel, 0) + 1
        sorted_items = sorted(counter.items(), key=lambda x: -x[1])[:limit]
        return [{"selector": sel, "count": cnt} for sel, cnt in sorted_items]

    @staticmethod
    def _priority_distribution(case_results: list[dict]) -> list[dict]:
        """优先级分布统计"""
        dist: dict[str, dict] = defaultdict(lambda: {"priority": "", "pass": 0, "fail": 0})
        for c in case_results:
            p = c["priority"]
            dist[p]["priority"] = p
            if c["final_status"] == "success":
                dist[p]["pass"] += 1
            elif c["final_status"] == "failed":
                dist[p]["fail"] += 1
        return sorted(dist.values(), key=lambda x: x["priority"])

    # ═══════════════════════════════════════════════
    # 渲染 + 文件管理
    # ═══════════════════════════════════════════════

    def _render(self, data: dict) -> str:
        """使用 Jinja2 渲染 HTML"""
        # 每次从文件加载模板（支持热更新）
        template = _env.get_template("report_template.html")
        return template.render(**data)

    def _save_html_file(self, execution_id: int, html: str) -> str:
        """保存 HTML 到 reports 目录"""
        report_dir = Path(settings.REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"execution_{execution_id}_report.html"
        file_path = report_dir / file_name
        file_path.write_text(html, encoding="utf-8")
        return f"/reports/{file_name}"

    def _save_db(
        self, execution_id: int, html: str, summary: dict, file_path: str
    ) -> Report:
        """保存/更新 execution_reports 记录"""
        existing = (
            self._db.query(Report)
            .filter(Report.execution_id == execution_id)
            .first()
        )
        if existing:
            existing.report_html = html[:50000]  # DB 中存储截断版本
            existing.report_summary = json.dumps(summary, ensure_ascii=False)
            existing.download_url = file_path
        else:
            existing = Report(
                execution_id=execution_id,
                report_html=html[:50000],
                report_summary=json.dumps(summary, ensure_ascii=False),
                download_url=file_path,
            )
            self._db.add(existing)

        self._db.commit()
        self._db.refresh(existing)
        return existing

    @staticmethod
    def _relative_path(absolute: str) -> str:
        """将绝对路径转为相对路径（HTML 中引用用 ../ 前缀，统一使用 / 分隔符）"""
        report_dir = Path(settings.REPORT_DIR).resolve()
        try:
            p = Path(absolute).resolve()
            return "../" + p.relative_to(report_dir.parent).as_posix()
        except ValueError:
            return absolute.replace("\\", "/")

    # ═══════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════

    def get_report_info(self, execution_id: int) -> Optional[dict]:
        """获取报告信息"""
        report = (
            self._db.query(Report)
            .filter(Report.execution_id == execution_id)
            .first()
        )
        if not report:
            return None
        return {
            "report_id": report.id,
            "execution_id": report.execution_id,
            "summary": (
                json.loads(report.report_summary)
                if report.report_summary else {}
            ),
            "download_url": report.download_url,
            "created_at": str(report.created_at) if report.created_at else "",
        }

    # ═══════════════════════════════════════════════
    # 报告清理
    # ═══════════════════════════════════════════════

    @staticmethod
    def cleanup_old_reports(max_days: int = 30) -> int:
        """清理超过 max_days 天的报告文件"""
        report_dir = Path(settings.REPORT_DIR)
        if not report_dir.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=max_days)
        deleted = 0
        for f in report_dir.glob("execution_*_report.html"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
                    deleted += 1
                    logger.info("清理过期报告: %s", f.name)
            except Exception:
                pass
        if deleted:
            logger.info("报告清理完成: 删除 %s 个过期文件", deleted)
        return deleted
