"""P0-5 验收 — KPI eligibility 标记（每个 terminal BatchCase 计算并落库 kpi_eligible）

验收 3 的 KPI 口径：
  kpi_eligible = 真实 AI attempt 发生 && non-mock && 非 deadline_excluded。
  first_gen_valid_count（70% 首生成有效率分子）仅计 kpi_eligible 且首次真实 AI 输出
  通过 Validator 的 case；validation_failed_count = kpi_eligible 且 is_valid_at_attempt=false。

禁止：不按 slot_timeout / circuit skip 计 KPI cohort；不按 Execution.status 做排除。
"""

from types import SimpleNamespace

from app.exceptions import AIException
from app.services.batch_generate_service import (
    BatchGenerateService,
    BatchJob,
    DBBatchPersistence,
)


def _new(sess_factory, process):
    return BatchGenerateService(
        process_case=process,
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )


# ═══════════════════════════════════════════════
# 场景 1：非 mock 首生成有效 → kpi_eligible=true、first_gen_valid
# ═══════════════════════════════════════════════

def test_non_mock_success_is_kpi_eligible(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")  # 真实模式（non-mock）

    svc = _new(sess_factory, lambda pid, cid, s, remaining=None: SimpleNamespace(code_id=11, is_valid=True))
    job = BatchJob("b", 1, [1])
    assert svc._claim(job) == 1
    svc._process(job, 1, sess_factory())
    svc._finalize(job)

    s = job.terminal_snapshot
    c = s["cases"][0]
    assert c["status"] == "success"
    assert c["kpi_eligible"] is True
    assert c["exclusion_bucket"] == "kpi_eligible"
    assert c["is_valid_at_attempt"] is True
    assert s["kpi_eligible_count"] == 1
    assert s["first_gen_valid_count"] == 1

    from app.models.batch_cases import BatchCase
    r = sess_factory().query(BatchCase).filter(BatchCase.case_id == 1).first()
    assert r.kpi_eligible == 1
    assert r.is_valid_at_attempt == 1


# ═══════════════════════════════════════════════
# 场景 2：mock（无 KEY）→ kpi_eligible=false、mock_excluded
# ═══════════════════════════════════════════════

def test_mock_success_not_kpi_eligible(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "")  # Mock 模式

    svc = _new(sess_factory, lambda pid, cid, s, remaining=None: SimpleNamespace(code_id=22, is_valid=True))
    job = BatchJob("b", 1, [1])
    assert svc._claim(job) == 1
    svc._process(job, 1, sess_factory())
    svc._finalize(job)

    s = job.terminal_snapshot
    c = s["cases"][0]
    assert c["status"] == "success"
    assert c["is_mock_at_attempt"] is True
    assert c["kpi_eligible"] is False
    assert c["exclusion_bucket"] == "mock_excluded"
    assert s["kpi_eligible_count"] == 0
    assert s["mock_excluded_count"] == 1

    from app.models.batch_cases import BatchCase
    r = sess_factory().query(BatchCase).filter(BatchCase.case_id == 1).first()
    assert r.kpi_eligible == 0


# ═══════════════════════════════════════════════
# 场景 3：slot_timeout（真实 HTTP 前的 pre-attempt 失败）→ 不进 KPI cohort
# ═══════════════════════════════════════════════

def test_slot_timeout_not_kpi_eligible(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")

    def raise_slot(pid, cid, s, remaining=None):
        raise AIException(error_type="slot_timeout", retryable=False)

    svc = _new(sess_factory, raise_slot)
    job = BatchJob("b", 1, [1])
    assert svc._claim(job) == 1
    svc._process(job, 1, sess_factory())
    svc._finalize(job)

    s = job.terminal_snapshot
    c = s["cases"][0]
    assert c["status"] == "failed"
    assert c["error_type"] == "slot_timeout"
    assert c["kpi_eligible"] is False
    assert c["exclusion_bucket"] == "pre_attempt_excluded"
    assert s["pre_attempt_excluded_count"] == 1
    assert s["kpi_eligible_count"] == 0


# ═══════════════════════════════════════════════
# 场景 4：真实 AI 输出但未过 Validator → 仍 KPI cohort（validation_failed）
# 《任务要求》：首生成有效率的分子只计首次真实输出通过者；首次不过只是不进分子，
# 但仍计入 cohort（kpi_eligible=true），对应 validation_failed_count。
# ═══════════════════════════════════════════════

def test_validation_failure_in_cohort_but_not_first_gen_valid(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")

    svc = _new(sess_factory, lambda pid, cid, s, remaining=None: SimpleNamespace(code_id=33, is_valid=False))
    job = BatchJob("b", 1, [1])
    assert svc._claim(job) == 1
    svc._process(job, 1, sess_factory())
    svc._finalize(job)

    s = job.terminal_snapshot
    c = s["cases"][0]
    assert c["status"] == "failed"
    assert c["error_type"] == "validation_error"
    assert c["is_valid_at_attempt"] is False
    assert c["kpi_eligible"] is True           # valid 真实 attempt、non-mock、非 deadline → 计入 cohort
    assert c["exclusion_bucket"] == "kpi_eligible"
    assert s["kpi_eligible_count"] == 1
    assert s["first_gen_valid_count"] == 0     # 首次输出未过 Validator → 不进分子
    assert s["validation_failed_count"] == 1