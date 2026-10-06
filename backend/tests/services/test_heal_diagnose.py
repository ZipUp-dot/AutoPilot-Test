"""heal 诊断工具验收 — 只读 + 双 schema 解析 + 证据可证分类

覆盖验收标准：
  1. fixture：3 条 HealRecord（success / timeout / candidate-failed）→ 诊断输出与 fixture 一致
  2. 脚本只读断言：诊断前后 HealRecord 行数不变（且 Execution/ExecutionStep 不变）
  3. attempts 双 schema（A 旧逐 step / B Case 级 Round）分别解析，禁止统一假设
  4. analysis_inference 标签只进报告，绝不写入平台字段 / 数据库
"""

import json
from datetime import datetime as dt

from scripts.heal_diagnose import (
    DIAGNOSIS_CATEGORIES,
    classify_failure,
    diagnose,
    parse_attempts,
    render_markdown,
)


def _seed(db, project, case, code):
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep

    exec_obj = Execution(
        project_id=project.id, total_cases=1, status="running", start_time=dt.utcnow(),
    )
    db.add(exec_obj)
    db.flush()
    step = ExecutionStep(
        execution_id=exec_obj.id, case_id=case.id, step_index=1,
        action="click", status="failed", error_type="element_not_found",
    )
    db.add(step)
    db.commit()
    return exec_obj, step


def _attempt_b(attempt, validator_result, rerun_result, rerun_error_type, candidate=None):
    return {
        "attempt": attempt,
        "candidate_code": candidate,
        "validator_result": validator_result,
        "validator_error": None,
        "rerun_result": rerun_result,
        "rerun_error_type": rerun_error_type,
        "rerun_message": "业务断言失败" if rerun_error_type else "",
        "error_message": None,
        "created_at": "2026-10-06T00:00:00",
    }


def _seed_three(db, project, case, code):
    """3 条 fixture：success / timeout / candidate-failed（均为 B schema）"""
    from app.models.heal_record import HealRecord

    exec_obj, step = _seed(db, project, case, code)

    success = HealRecord(
        execution_step_id=step.id, execution_id=exec_obj.id, case_id=case.id, round_no=1,
        root_execution_step_id=step.id, retry_status="success", retry_count=1, error_type=None,
        attempts=json.dumps([_attempt_b(1, True, "success", None, "async def run_test(safe): ...")]),
    )
    timeout = HealRecord(
        execution_step_id=step.id, execution_id=exec_obj.id, case_id=case.id, round_no=2,
        root_execution_step_id=step.id, retry_status="failed", retry_count=1,
        error_type="deadline_exceeded",
        attempts=json.dumps([_attempt_b(1, False, None, "deadline_exceeded")]),
    )
    candidate_failed = HealRecord(
        execution_step_id=step.id, execution_id=exec_obj.id, case_id=case.id, round_no=3,
        root_execution_step_id=step.id, retry_status="failed", retry_count=3,
        error_type="heal_exhausted",
        attempts=json.dumps([
            _attempt_b(1, True, "failed", "case_rerun_failed", "async def run_test(safe): ..."),
            _attempt_b(2, True, "failed", "case_rerun_failed", "async def run_test(safe): ..."),
            _attempt_b(3, False, None, None),
        ]),
    )
    db.add_all([success, timeout, candidate_failed])
    db.commit()
    return exec_obj, step


class TestDiagnoseFixture:
    def test_three_records_match_fixture(self, db_session, sample_project, sample_test_case, sample_generated_code):
        """3 条 fixture → 分类/outcome 与 fixture 一致"""
        _seed_three(db_session, sample_project, sample_test_case, sample_generated_code)

        result = diagnose(db_session)

        assert result["heal_records_total"] == 3
        by_round = {r["round_no"]: r for r in result["records"]}

        # Round 1：成功 → outcome=success，无诊断分类
        assert by_round[1]["outcome"] == "success"
        assert by_round[1]["diagnosis_category"] is None

        # Round 2：deadline → deadline_timeout
        assert by_round[2]["outcome"] == "failed"
        assert by_round[2]["diagnosis_category"] == "deadline_timeout"
        assert by_round[2]["heal_error_type"] == "deadline_exceeded"
        assert by_round[2]["root_step_error_type"] == "element_not_found"  # 路由层真实字段

        # Round 3：heal_exhausted → candidate_rerun_failed（且推断标签只在 analysis_inference）
        assert by_round[3]["diagnosis_category"] == "candidate_rerun_failed"
        assert by_round[3]["analysis_inference"] == ["candidate_wrong (analysis_inference)"]

        # 分类分布
        assert result["diagnosis_category_counts"]["deadline_timeout"] == 1
        assert result["diagnosis_category_counts"]["candidate_rerun_failed"] == 1
        # 分类闭集：所有非空分类必在 DIAGNOSIS_CATEGORIES 内
        for r in result["records"]:
            if r["diagnosis_category"] is not None:
                assert r["diagnosis_category"] in DIAGNOSIS_CATEGORIES

        # 全部为 B schema
        assert result["attempts_schema_counts"]["B"] == 5
        assert result["attempts_schema_counts"]["A"] == 0

    def test_script_is_read_only(self, db_session, sample_project, sample_test_case, sample_generated_code):
        """只读断言：诊断前后 HealRecord / Execution / ExecutionStep 行数不变"""
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.models.heal_record import HealRecord

        _seed_three(db_session, sample_project, sample_test_case, sample_generated_code)

        def snapshot():
            return (
                db_session.query(HealRecord).count(),
                db_session.query(Execution).count(),
                db_session.query(ExecutionStep).count(),
            )

        before = snapshot()
        assert before[0] == 3

        result = diagnose(db_session)
        render_markdown(result)   # 渲染不应触发任何写操作

        # diagnose 为纯 SELECT（无 commit/flush），行数必须逐表一致
        assert snapshot() == before

    def test_inference_never_written_to_db(self, db_session, sample_project, sample_test_case, sample_generated_code):
        """analysis_inference 只进报告，不写回 err_type / 任何平台字段"""
        from app.models.heal_record import HealRecord

        _seed_three(db_session, sample_project, sample_test_case, sample_generated_code)
        before_errors = {r.id: r.error_type for r in db_session.query(HealRecord).all()}

        result = diagnose(db_session)
        md = render_markdown(result)
        assert "candidate_wrong (analysis_inference)" in md

        db_session.expire_all()
        after_errors = {r.id: r.error_type for r in db_session.query(HealRecord).all()}
        assert after_errors == before_errors
        # 平台字段中不得出现推断标签
        assert all(v not in ("candidate_wrong",) for v in after_errors.values())


class TestAttemptsSchemaBranch:
    def test_parse_a_schema(self):
        """旧写入方 A：generated_code / status / error → schema=A"""
        raw = json.dumps([{
            "attempt": 1, "generated_code": "def run_test(driver): ...",
            "status": "retrying", "error": "", "created_at": "2026-10-06T00:00:00",
        }])
        parsed = parse_attempts(raw)
        assert parsed[0]["_schema"] == "A"

    def test_parse_b_schema(self):
        """Round 写入方 B：candidate_code / validator_result / rerun_result → schema=B"""
        raw = json.dumps([_attempt_b(1, True, "success", None, "code")])
        parsed = parse_attempts(raw)
        assert parsed[0]["_schema"] == "B"

    def test_a_schema_record_classified_by_error_type(self, db_session, sample_project, sample_test_case, sample_generated_code):
        """A schema 无 validator/rerun 事实 → 分类回退到 HealRecord.error_type"""
        from app.models.heal_record import HealRecord

        _, step = _seed(db_session, sample_project, sample_test_case, sample_generated_code)
        rec = HealRecord(
            execution_step_id=step.id, retry_status="failed", retry_count=1,
            error_type="ai_request_failed",
            attempts=json.dumps([{
                "attempt": 1, "generated_code": None, "status": "failed",
                "error": "AI 调用失败(已重试3次)", "created_at": "2026-10-06T00:00:00",
            }]),
        )
        db_session.add(rec)
        db_session.commit()

        result = diagnose(db_session)
        assert result["attempts_schema_counts"]["A"] == 1
        assert result["records"][0]["diagnosis_category"] == "ai_request_failed"

    def test_cancelled_maps_to_cancelled(self):
        """cancelled_by_recovery → cancelled（证据 = retry_status）"""
        class _Rec:
            retry_status = "cancelled_by_recovery"
            error_type = None

        assert classify_failure(_Rec(), []) == "cancelled"

    def test_unknown_schema_not_guessed(self):
        """无判别键 → schema=unknown（保守，不猜 A/B）"""
        parsed = parse_attempts(json.dumps([{"attempt": 1}]))
        assert parsed[0]["_schema"] == "unknown"
