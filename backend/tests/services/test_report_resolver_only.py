"""Report 状态真源唯一化测试（BUG-REPORT-RESOLVER）

AC-01 静态：report_service 不得 import/引用 case 状态判定函数（白名单 CaseStateResolver
           除外），且不得存在「Report 自行重解释 case 状态」的自判兜底分支。
AC-03 边界：interrupted / skipped 等边界 case 的报告渲染值与 CaseStateResolver 输出一致。

RED 先行：本文件先于实现落地（新行为不存在 → 失败），实现后 GREEN。
"""

import inspect
import json
import re
from datetime import datetime

import pytest

from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.services.report_service import ReportService
from app.utils.case_state_resolver import resolve, step_to_dict


# ═══════════════════════════════════════════════
# AC-01：report 内零自判分支（静态检查）
# ═══════════════════════════════════════════════

class TestNoSelfJudgingBranch:
    """AC-01 静态检查：report_service 不得 import/引用 case 状态判定函数"""

    def test_whitelisted_resolver_is_referenced(self):
        """白名单唯一解释器 = CaseStateResolver（必须被引用）"""
        import app.services.report_service as rs

        src = inspect.getsource(rs)
        assert "case_state_resolver" in src

    def test_no_forbidden_status_judger(self):
        """其他 case 状态判定函数/映射一律禁止引用"""
        import app.services.report_service as rs

        src = inspect.getsource(rs)
        assert "map_terminal_reason" not in src
        assert "from app.utils import terminal_reason" not in src

    def test_no_self_judging_fallback(self):
        """不得存在「非 success/failed/skipped → skipped」的自判兜底分支"""
        import app.services.report_service as rs

        src = inspect.getsource(rs)
        assert not re.search(r'final_status\s*=\s*["\']skipped["\']', src)
        assert not re.search(r'not in\s*\(\s*["\']success["\']', src)


# ═══════════════════════════════════════════════
# AC-03：渲染与 Resolver 输出一致
# ═══════════════════════════════════════════════

def _make_execution(db, project_id, *, runtime_state=None, status="completed"):
    ex = Execution(
        project_id=project_id,
        total_cases=0,
        status=status,
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow(),
        runtime_state_json=json.dumps(runtime_state if runtime_state is not None else {}),
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


def _add_steps(db, execution_id, case_id, specs):
    for i, spec in enumerate(specs):
        db.add(ExecutionStep(
            execution_id=execution_id,
            case_id=case_id,
            step_index=i,
            action="click",
            status=spec["status"],
            error_type=spec.get("error_type"),
            skip_reason=spec.get("skip_reason"),
            exception_type=spec.get("exception_type"),
            duration_ms=10,
        ))
    db.commit()


def _rendered_final_status(svc, execution_id):
    _html, data = svc._render_report(execution_id)
    assert len(data["cases"]) == 1
    return data["cases"][0]["final_status"]


BOUNDARY_SCENARIOS = [
    pytest.param([{"status": "skipped", "skip_reason": "interrupted"}], id="interrupted-skip"),
    pytest.param([{"status": "skipped", "skip_reason": "user_stopped"}], id="user-stopped-skip"),
    pytest.param([{"status": "skipped", "skip_reason": "circuit_open"}], id="circuit-open-skip"),
    pytest.param([{"status": "skipped"}], id="plain-skip"),
    pytest.param([{"status": "failed", "error_type": "business_assertion_failed"}], id="business-failed"),
    pytest.param([{"status": "failed", "error_type": "element_not_found"}], id="platform-failed"),
    pytest.param([{"status": "success"}], id="success"),
    pytest.param([{"status": "pending"}], id="pending"),
    pytest.param([{"status": "weird"}], id="malformed"),
    pytest.param([{"status": "success"}, {"status": "skipped", "skip_reason": "circuit_open"}], id="incomplete"),
]


class TestRenderMatchesResolver:
    """AC-03：报告渲染的 case 终态与 CaseStateResolver 输出逐场景一致"""

    @pytest.mark.parametrize("steps", BOUNDARY_SCENARIOS)
    def test_final_status_matches_resolver(
        self, db_session, sample_project, sample_test_case, steps
    ):
        ex = _make_execution(db_session, sample_project.id)
        _add_steps(db_session, ex.id, sample_test_case.id, steps)

        svc = ReportService(db_session)
        rendered = _rendered_final_status(svc, ex.id)

        rows = (
            db_session.query(ExecutionStep)
            .filter(ExecutionStep.execution_id == ex.id)
            .order_by(ExecutionStep.step_index)
            .all()
        )
        expected, _reason = resolve([step_to_dict(r) for r in rows])
        assert rendered == expected

    def test_sealed_case_status_not_trusted_over_resolver(
        self, db_session, sample_project, sample_test_case
    ):
        """sealed case_status 与步骤事实冲突时以 Resolver 为准（无第二解释路径）"""
        ex = _make_execution(
            db_session, sample_project.id,
            runtime_state={str(sample_test_case.id): {"case_status": "skipped"}},
        )
        _add_steps(db_session, ex.id, sample_test_case.id, [{"status": "success"}])

        svc = ReportService(db_session)
        assert _rendered_final_status(svc, ex.id) == "success"
