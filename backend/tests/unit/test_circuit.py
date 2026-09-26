"""P0-5 验收 — Circuit 熔断（按 case 终态事件，仅 non-mock，铁律 22）

验收覆盖：
  1. streak 语义：3 个连续 non-mock retryable 失败→熔断；中途 mock success 不打断；
     success 清零。
  2. slot_timeout / circuit skip 不计 streak、不进 KPI cohort。

使用直接构造 BatchJob + 驱动 service._terminalize/_finalize（不启动 worker），
确定性验证电路事件消费；摘summary 走 InMemory persistence。
"""

import pytest

from app.services.batch_generate_service import (
    BatchGenerateService,
    BatchJob,
    InMemoryBatchPersistence,
)


def build_job(case_ids, batch_id="b"):
    return BatchJob(batch_id, 1, list(case_ids))


def build_service():
    return BatchGenerateService(
        process_case=lambda *a, **k: None,
        session_factory=lambda: None,
        persistence=InMemoryBatchPersistence(),
    )


def _retryable(svc, job, cid):
    svc._terminalize(
        job, cid,
        status="failed", error_type="generation_failed",
        is_mock_at_attempt=False, attempt_count=1,
        exclusion_bucket="kpi_eligible", circuit_event="retryable_failure",
    )


def _success(svc, job, cid):
    svc._terminalize(
        job, cid,
        status="success", error_type=None,
        is_mock_at_attempt=False, attempt_count=1,
        exclusion_bucket="kpi_eligible", circuit_event="success",
    )


def _mock_success(svc, job, cid):
    svc._terminalize(
        job, cid,
        status="success", error_type=None,
        is_mock_at_attempt=True, attempt_count=0,
        exclusion_bucket="mock_excluded", circuit_event="mock_success",
    )


def _slot_timeout(svc, job, cid):
    svc._terminalize(
        job, cid,
        status="failed", error_type="slot_timeout",
        is_mock_at_attempt=False, attempt_count=0,
        exclusion_bucket="pre_attempt_excluded", circuit_event="non_retryable_failure",
    )


# ═══════════════════════════════════════════════
# 验收 1a：3 个连续 non-mock retryable 失败 → 熔断，剩余 pending → skipped(circuit_open)
# ═══════════════════════════════════════════════

def test_three_consecutive_retryable_failures_open_circuit(mock_settings):
    mock_settings("OPENAI_API_KEY", "test-key")  # 非 mock：使 _finalize 的 pending-skip 走 skip_reason 分桶
    svc = build_service()
    job = build_job([1, 2, 3, 4, 5])
    for cid in (1, 2, 3):
        _retryable(svc, job, cid)

    assert job.streak == 3
    assert job.circuit_open is True

    # 剩余 pending → 收口为 skipped(circuit_open)，不进 KPI cohort
    svc._finalize(job)
    assert job.terminal_snapshot["status"] == "failed"
    skipped = [c for c in job.terminal_snapshot["cases"] if c["status"] == "skipped"]
    assert len(skipped) == 2
    assert all(c["error_type"] == "circuit_open" for c in skipped)
    assert all(c["exclusion_bucket"] == "pre_attempt_excluded" for c in skipped)


# ═══════════════════════════════════════════════
# 验收 1c：success 清零 streak；需重新累积 3 次才再熔断
# ═══════════════════════════════════════════════

def test_success_resets_streak():
    svc = build_service()
    job = build_job([1, 2, 3, 4, 5])
    _retryable(svc, job, 1)   # streak=1
    _success(svc, job, 2)     # streak=0
    assert job.streak == 0, "success 应清零 streak"
    _retryable(svc, job, 3)   # streak=1
    _retryable(svc, job, 4)   # streak=2
    _retryable(svc, job, 5)   # streak=3

    assert job.streak == 3
    assert job.circuit_open is True


# ═══════════════════════════════════════════════
# 验收 1b：中途 mock success 不打断（不重置）streak
# ═══════════════════════════════════════════════

def test_mock_success_does_not_break_streak():
    svc = build_service()
    job = build_job([1, 2, 3, 4])
    _retryable(svc, job, 1)       # non-mock retryable → streak=1
    _mock_success(svc, job, 2)    # mock success → streak 不变（仍 1）
    assert job.streak == 1, "mock success 不应清零 streak"

    _retryable(svc, job, 3)       # streak=2
    _retryable(svc, job, 4)       # streak=3 → 熔断
    assert job.streak == 3
    assert job.circuit_open is True


# ═══════════════════════════════════════════════
# 验收 2：slot_timeout 不计 streak、不进 KPI cohort
# ═══════════════════════════════════════════════

def test_slot_timeout_not_counted_streak_or_cohort():
    svc = build_service()
    job = build_job([1, 2, 3])
    for cid in (1, 2, 3):
        _slot_timeout(svc, job, cid)

    # slot_timeout（pre-attempt）不计 streak → 永不熔断
    assert job.streak == 0
    assert job.circuit_open is False

    svc._finalize(job)
    cases = {c["case_id"]: c for c in job.terminal_snapshot["cases"]}
    assert all(cases[c]["exclusion_bucket"] == "pre_attempt_excluded" for c in (1, 2, 3))
    assert job.terminal_snapshot["kpi_eligible_count"] == 0
    assert job.terminal_snapshot["pre_attempt_excluded_count"] == 3


# ═══════════════════════════════════════════════
# 验收 2：circuit skip 不比 streak、不进 KPI cohort
# ═══════════════════════════════════════════════

def test_circuit_skip_not_counted_streak_or_cohort():
    svc = build_service()
    job = build_job([1, 2, 3, 4, 5])
    _retryable(svc, job, 1)
    _retryable(svc, job, 2)
    _retryable(svc, job, 3)   # circuit_open

    with job.lock:
        job.abort_requested = True
        job.circuit_open = True
    # circuit_is_open 后的 skipped 事件不改 streak
    for cid in (4, 5):
        snap = job.cases[cid]
        _skip_with(job, snap)

    assert job.streak == 3
    svc._finalize(job)
    assert job.terminal_snapshot["kpi_eligible_count"] == 3  # 仅前 3 个 failed 计入
    assert job.terminal_snapshot["pre_attempt_excluded_count"] == 2


def _skip_with(job, snap):
    snap.phase = "terminal"
    snap.status = "skipped"
    snap.error_type = "circuit_open"
    snap.exclusion_bucket = "pre_attempt_excluded"
    snap.circuit_event = "skipped"
    snap.kpi_eligible = False
    snap.processed = False
    snap.ai_attempted = False
    job.skipped += 1