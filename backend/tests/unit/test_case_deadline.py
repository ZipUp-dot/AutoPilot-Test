"""P0-5 验收 — Case Deadline 双层（铁律 21）

验收覆盖（验收 3）：
  (a) claim 前短路：batch_remaining<=0 → 禁止 claim，剩余 pending → skipped(deadline_exceeded)，
      skip_reason 统一写 "deadline_exceeded"，禁止出现两种取值。
  (b) 在途 overrun：返回时已越 case_deadline → 本次结果记 deadline_exceeded；
      有效代码仍落库（供未来 Admission 复用），batch_cases 标记 kpi_eligible=false。

命名钉死：claim 前检查的量叫 batch_remaining，claim 后的叫 case_remaining。
"""

import time
from types import SimpleNamespace

from app.services.batch_generate_service import (
    BatchGenerateService,
    BatchJob,
    DBBatchPersistence,
)


# ═══════════════════════════════════════════════
# 验收 3a：claim 前短路（batch_remaining<=0）→ 全部 pending → skipped(deadline_exceeded)
# ═══════════════════════════════════════════════

def test_batch_deadline_short_circuit_before_claim(file_db, mock_settings):
    mock_settings("OPENAI_API_KEY", "test-key")  # 非 mock：使 _finalize 的 pending-skip 归 deadline_excluded
    eng, sess_factory = file_db
    svc = BatchGenerateService(
        process_case=lambda *a, **k: None,
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    job = BatchJob("b", 1, [1, 2, 3])
    # 强制 Batch 已到期：claim 前 batch_remaining<=0
    job.batch_deadline = time.monotonic() - 1.0

    assert svc._claim(job) is None
    assert job.batch_expired is True

    svc._finalize(job)
    summary = job.terminal_snapshot
    assert summary["status"] == "failed"
    skipped = [c for c in summary["cases"] if c["status"] == "skipped"]
    assert len(skipped) == 3
    # skip_reason 统一为 deadline_exceeded，且只此一种取值
    assert all(c["error_type"] == "deadline_exceeded" for c in skipped)
    assert all(c["exclusion_bucket"] == "deadline_excluded" for c in skipped)
    assert summary["deadline_excluded_count"] == 3
    assert summary["kpi_eligible_count"] == 0


# ═══════════════════════════════════════════════
# 验收 3b：在途 overrun → deadline_exceeded，有效代码落库且 kpi_eligible=false
# ═══════════════════════════════════════════════

def test_inflight_overrun_valid_code_persisted_not_kpi(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")  # 真实模式（non-mock）

    def sleepy(pid, cid, session, remaining=None):
        time.sleep(0.35)
        return SimpleNamespace(code_id=777, is_valid=True)

    svc = BatchGenerateService(
        process_case=sleepy,
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    job = BatchJob("b", 1, [1])
    # case_deadline = min(batch_deadline, claim+case_budget)：把 batch_deadline 卡近
    job.batch_deadline = time.monotonic() + 0.2

    assert svc._claim(job) == 1
    svc._process(job, 1, sess_factory())
    svc._finalize(job)

    summary = job.terminal_snapshot
    c = summary["cases"][0]
    assert c["status"] == "failed"
    assert c["error_type"] == "deadline_exceeded"
    assert c["code_id"] == 777          # 有效代码保留（generate_single 已落库，供复用）
    assert c["exclusion_bucket"] == "deadline_excluded"
    assert c["kpi_eligible"] is False   # 本次不计 KPI

    from app.models.batch_cases import BatchCase
    rows = sess_factory().query(BatchCase).filter(BatchCase.batch_id == "b").all()
    assert len(rows) == 1
    r = rows[0]
    assert r.code_id == 777
    assert r.error_type == "deadline_exceeded"
    assert r.kpi_eligible == 0          # overrun 落库时 kpi_eligible 钉为 false
    assert r.is_valid_at_attempt == 1   # 首次真实 AI 输出有效（仅被 deadline 排除出 cohort）
    assert r.terminal_at is not None