"""测试报告服务 — 数据聚合 + Jinja2 渲染 + 文件管理 + 过期清理

数据源（P0-10 事实源钉死）:
  - executions 表：批次信息、总耗时、执行模式
  - executions.manifest_json：环境/名称快照（project name/target_url，Admission 时
    冻结；改 Project 后旧报告不变）
  - executions.runtime_state_json：case 终态事实源（Seal 时 Resolver 计算一次写入；
    Seal 后 Report/Metrics/Detail 只读 sealed case_status + terminal_reason，
    禁止重跑 Resolver）
  - execution_steps 表：每步的执行状态、截图、日志、错误
  - test_cases 表：用例名称、优先级、预期结果
  - heal_records 表：自愈记录

报告终态分型（映射钉死，禁止自定义值）:
  completed→full、stopped→partial、failed→diagnostic、interrupted→interrupted；
  非终态 status（queued/running/healing）兜底 full。

Report Claim（幂等 + 可恢复 + owner fencing）:
  状态机 generating→ready/failed、failed→generating（不存在 pending 态，claim 即
  创建 generating）。claim 逻辑：
    - 不存在 → 创建 generating + 唯一 claim_token
    - ready → 直接复用（artifact 文件缺失时转 failed 重新 claim，保留恢复语义）
    - generating 未超时 → ReportClaimConflict（路由映射 409）
    - generating 已超时 → reclaim（生成新 claim_token，旧 token 立即失效）
    - failed → 重新 claim（generating + 新 token）
  每次 claim 内部最多一次生成尝试：生成异常 → fenced 置 failed 再抛（模板错误/
  路径不可写等永久故障不得空转，等待人工/monitor 再次触发）。

  Report Claim Fencing：生成完成/失败写回必须携带本次 claim_token，更新条件 =
  WHERE execution_id=? AND report_type=? AND generation_status='generating' AND
  claim_token=<本次 token>——旧 owner 在 reclaim 后晚到，其写回因 token 失效而
  影响行数=0，不得覆盖新 owner 结果。
  Report Artifact Fencing：每次 claim 的临时文件与最终 artifact 路径绑定 token
  （report_{id}_{type}_{token}.tmp → os.replace 原子 rename → .html），不同 claim
  禁止共享同一最终文件路径；只有当前 token 的 owner 才能发布 canonical artifact，
  旧 owner 晚到的文件只能成为孤立文件、不得覆盖当前 owner 的可见报告。
"""

import json
import logging
import os
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import update as _sa_update
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
# 防止同一 execution_id 同时进入 claim 状态机（DB 层另由 fenced UPDATE 兜底竞态）。
_REPORT_LOCK = threading.Lock()

# 报告终态分型映射（P0-10，钉死，禁止自定义值）
REPORT_TYPES = {
    "completed": "full",
    "stopped": "partial",
    "failed": "diagnostic",
    "interrupted": "interrupted",
}


class ReportClaimConflict(Exception):
    """report_type 的生成 claim 冲突：该行仍处于 generating 且未超时（另一 owner 在途）"""


def report_type_for_status(status: str) -> str:
    """Execution.status → report_type；非终态 status（queued/running/healing）兜底 full"""
    return REPORT_TYPES.get(status, "full")


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
    # 报告生成（claim 状态机 + owner fencing）
    # ═══════════════════════════════════════════════

    def generate(self, execution_id: int) -> dict:
        """生成 HTML 报告（进程内互斥 + claim 状态机）

        Returns:
            { "report_id": 1, "download_url": "..." }
        """
        with _REPORT_LOCK:
            return self._generate(execution_id)

    def _generate(self, execution_id: int) -> dict:
        """生成 HTML 报告（锁内执行）"""
        t0 = time.time()

        execution = (
            self._db.query(Execution)
            .filter(Execution.id == execution_id)
            .first()
        )
        if not execution:
            raise ValueError(f"执行批次 {execution_id} 不存在")

        report_type = report_type_for_status(execution.status)

        # claim 状态机（claim 即创建/复用 generating 行；无 pending 态）
        report, claim_token = self._claim(execution, report_type)
        if report.generation_status == "ready":
            logger.info("报告已存在(ready): execution_id=%s type=%s", execution_id, report_type)
            return {
                "report_id": report.id,
                "download_url": report.download_url or "",
            }

        # 每次 claim 内部最多一次生成尝试（禁止内部自动无限重试；
        # 永久故障置 failed，等待人工/monitor 再次触发）
        try:
            html, report_data = self._render_report(execution_id)
        except Exception as e:
            self._mark_failed(execution, report_type, claim_token)
            logger.exception("报告渲染失败: execution_id=%s type=%s", execution_id, report_type)
            raise

        # Report Artifact Fencing：临时文件与最终 artifact 路径绑定 claim_token，
        # 原子 rename 发布；不同 claim 禁止共享同一最终文件路径。
        report_dir = Path(settings.REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"report_{execution_id}_{report_type}_{claim_token}.html"
        tmp_name = f"report_{execution_id}_{report_type}_{claim_token}.tmp"
        final_path = report_dir / file_name
        tmp_path = report_dir / tmp_name
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp_path, final_path)
        download_url = f"/reports/{file_name}"

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

        # fenced 写回（Report Claim Fencing）：仅当仍 generating 且 token 匹配
        report = self._mark_ready(
            execution, report_type, claim_token, html, summary, download_url
        )
        if report is None:
            # 旧 owner 晚到（claim_token 已失效被 reclaim）：不得覆盖新 owner 结果
            logger.warning(
                "报告 claim 已失效（旧 token 晚到），不覆盖新 owner 结果: "
                "execution_id=%s type=%s token=%s", execution_id, report_type, claim_token,
            )
            return {"report_id": None, "download_url": download_url}

        elapsed = time.time() - t0
        logger.info(
            "报告生成完成: execution_id=%s type=%s elapsed=%.2fs size=%s KB",
            execution_id, report_type, elapsed, len(html) // 1024,
        )
        return {
            "report_id": report.id,
            "download_url": download_url,
        }

    def _claim(self, execution: Execution, report_type: str) -> tuple[Report, str]:
        """claim 状态机（无 pending 态；超时 reclaim 生成新 token，旧 token 立即失效）

        Returns:
            (report_row, claim_token)。generation_status=='ready' 表示直接复用。
        Raises:
            ReportClaimConflict: generating 且未超时（另一 owner 在途）。
        """
        now = datetime.utcnow()
        existing = (
            self._db.query(Report)
            .filter(
                Report.execution_id == execution.id,
                Report.report_type == report_type,
            )
            .first()
        )
        token = uuid.uuid4().hex
        timeout = settings.REPORT_CLAIM_TIMEOUT_SECONDS

        # 不存在 → 创建 generating + 唯一 claim_token
        if existing is None:
            report = Report(
                execution_id=execution.id,
                report_type=report_type,
                generation_status="generating",
                claim_token=token,
                claimed_at=now,
            )
            self._db.add(report)
            self._db.commit()
            self._db.refresh(report)
            return report, token

        # ready → 直接复用；artifact 文件缺失（清理/人工删除）→ 转 failed 重新 claim
        if existing.generation_status == "ready":
            if self._artifact_exists(existing):
                return existing, existing.claim_token or token
            existing.generation_status = "failed"
            self._db.commit()

        # generating → 未超时拒绝；已超时 reclaim（新 token）
        if existing.generation_status == "generating":
            if existing.claimed_at is not None and (
                now - existing.claimed_at
            ).total_seconds() < timeout:
                raise ReportClaimConflict(
                    f"报告正在生成中（type={report_type}），请稍后重试"
                )
            existing.claim_token = token
            existing.claimed_at = now
            self._db.commit()
            return existing, token

        # failed → 重新 claim（generating + 新 token）
        existing.generation_status = "generating"
        existing.claim_token = token
        existing.claimed_at = now
        self._db.commit()
        return existing, token

    def _mark_ready(
        self,
        execution: Execution,
        report_type: str,
        token: str,
        html: str,
        summary: dict,
        download_url: str,
    ) -> Optional[Report]:
        """fenced 写回：仅当行仍处于 generating 且 claim_token==本次 token 才置 ready。

        影响行数=0 → 该 claim 已被 reclaim（token 失效）→ 返回 None，旧 owner 不得覆盖。
        """
        result = self._db.execute(
            _sa_update(Report)
            .where(
                Report.execution_id == execution.id,
                Report.report_type == report_type,
                Report.generation_status == "generating",
                Report.claim_token == token,
            )
            .values(
                report_html=html[:50000],  # DB 中存储截断版本
                report_summary=json.dumps(summary, ensure_ascii=False),
                download_url=download_url,
                generation_status="ready",
                claimed_at=datetime.utcnow(),
            )
        )
        self._db.commit()
        if result.rowcount == 0:
            return None
        report = (
            self._db.query(Report)
            .filter(
                Report.execution_id == execution.id,
                Report.report_type == report_type,
            )
            .first()
        )
        return report

    def _mark_failed(self, execution: Execution, report_type: str, token: str) -> None:
        """fenced 置 failed（同 _mark_ready 的 fencing 语义；生成异常时调用）"""
        self._db.execute(
            _sa_update(Report)
            .where(
                Report.execution_id == execution.id,
                Report.report_type == report_type,
                Report.generation_status == "generating",
                Report.claim_token == token,
            )
            .values(generation_status="failed")
        )
        self._db.commit()

    @staticmethod
    def _artifact_exists(report: Report) -> bool:
        """artifact 文件是否存在（ready 复用判定；download_url 指向文件已被清理则重渲染）"""
        if not report.download_url:
            return False
        return (Path(settings.REPORT_DIR) / Path(report.download_url).name).exists()

    # ═══════════════════════════════════════════════
    # 数据聚合（事实源 = sealed runtime_state + Manifest 快照）
    # ═══════════════════════════════════════════════

    def _render_report(self, execution_id: int) -> tuple[str, dict]:
        """查询执行数据、聚合、渲染 HTML（生成尝试主体）

        Returns:
            (html, report_data)：report_data 供 summary 使用，避免重复聚合。
        """
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

        steps = (
            self._db.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == execution_id)
            .order_by(ExecutionStep.case_id, ExecutionStep.step_index)
            .all()
        )

        case_ids = list(set(s.case_id for s in steps))
        cases_map = {
            c.id: c
            for c in self._db.query(TestCase)
            .filter(TestCase.id.in_(case_ids))
            .all()
        } if case_ids else {}

        step_ids = [s.id for s in steps]
        heal_records: dict[int, list] = {}
        if step_ids:
            for hr in (
                self._db.query(HealRecord)
                .filter(HealRecord.execution_step_id.in_(step_ids))
                .all()
            ):
                heal_records.setdefault(hr.execution_step_id, []).append(hr)

        # 最终代码统一走 ExecutionCodeResolver：只读 runtime_state 冻结的 active_code_id
        gen_codes: dict[int, GeneratedCode] = {}
        if case_ids:
            from app.services.execution_code_resolver import ExecutionCodeResolver
            resolver = ExecutionCodeResolver(self._db)
            for cid in case_ids:
                code = resolver.get_active_code(execution_id, cid)
                if code is not None:
                    gen_codes[cid] = code

        report_data = self._aggregate(
            execution, project, steps, cases_map, heal_records, gen_codes
        )
        return self._render(report_data), report_data

    def _aggregate(
        self,
        execution: Execution,
        project: Optional[Project],
        steps: list[ExecutionStep],
        cases_map: dict[int, TestCase],
        heal_records: dict[int, list[HealRecord]],
        gen_codes: dict[int, GeneratedCode],
    ) -> dict:
        """聚合所有数据为报告数据结构。

        case 终态两阶段钉死：Seal 时 Resolver 算一次写入 runtime_state → 此处只读
        sealed case_status + terminal_reason，禁止重跑 Resolver、禁止自判状态。
        """
        # ── 概览统计 ──
        case_steps = defaultdict(list)
        for s in steps:
            case_steps[s.case_id].append(s)

        # sealed runtime_state（唯一持久化真源；无 case_status 的 case 报告归 skipped）
        runtime_state: dict = {}
        if execution.runtime_state_json:
            try:
                runtime_state = json.loads(execution.runtime_state_json)
            except (TypeError, ValueError):
                runtime_state = {}

        # Manifest project 快照（环境/名称；Admission 时冻结，改 Project 后旧报告不变）
        manifest: dict = {}
        if execution.manifest_json:
            try:
                manifest = json.loads(execution.manifest_json)
            except (TypeError, ValueError):
                manifest = {}
        mproj = manifest.get("project") or {}
        project_name = mproj.get("name") or (project.name if project else "")
        target_url = mproj.get("target_url") or (
            project.target_url if project else ""
        )

        case_results = []
        passed = failed = skipped = 0
        total_duration_ms = 0

        for case_id, case_steps_list in case_steps.items():
            case = cases_map.get(case_id)
            if not case:
                continue

            entry = runtime_state.get(str(case_id), {}) or {}
            final_status = entry.get("case_status") or "unknown"
            # 报告展示值域仅 success/failed/skipped；unknown/pending/running 兜底 skipped
            if final_status not in ("success", "failed", "skipped"):
                final_status = "skipped"
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
            # 模板变量（project 快照来自 Manifest，保证改 Project 后旧报告不变）
            "project_name": project_name,
            "target_url": target_url,
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
        """获取报告信息（按 execution 当前 status 对应的 report_type 查询）

        Seal 后 status 冻结 → 查询目标唯一；非终态（极少直接查询场景）兜底 full。
        """
        execution = (
            self._db.query(Execution)
            .filter(Execution.id == execution_id)
            .first()
        )
        report_type = report_type_for_status(execution.status) if execution else "full"
        report = (
            self._db.query(Report)
            .filter(
                Report.execution_id == execution_id,
                Report.report_type == report_type,
            )
            .first()
        )
        if not report:
            return None
        return {
            "report_id": report.id,
            "execution_id": report.execution_id,
            "report_type": report.report_type,
            "generation_status": report.generation_status,
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
        """清理超过 max_days 天的报告文件（兼容新旧命名：execution_*_report.html 与
        token 绑定的 report_*.html；token 孤立文件同样按 mtime 清理）"""
        report_dir = Path(settings.REPORT_DIR)
        if not report_dir.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=max_days)
        deleted = 0
        for pattern in ("execution_*_report.html", "report_*.html"):
            for f in report_dir.glob(pattern):
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
