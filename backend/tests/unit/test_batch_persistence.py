"""P0-4 验收 — batch_cases 表 + batch_records 表 + 写入顺序

覆盖验收标准：
  1. Batch 完成后 batch_cases 逐行落库，字段与内存终态一致
  2. batch_records UNIQUE(batch_id)：重复写入被拒/可幂等 UPSERT
  3. 注入 batch_records 写失败 → BatchJob 不 completed，可重试且最终一致
  4. 模拟 commit 成功后崩溃 → 重启后 status 返回 task_lost 但能查到 batch_records
  5. BatchCase terminal 后修改其 GeneratedCode 的 is_valid → batch_cases 行不变
  6. 计数定义逐项验证（构造 mock / deadline / slot_timeout 各一 case）

使用文件版 SQLite（WAL + busy_timeout）支持多线程 worker 落库与跨会话读取；
显式构造 DBBatchPersistence，不依赖共享 singleton（其测试态已被重置为 InMemory）。
"""

import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.exceptions import AIException
from app.services.batch_generate_service import (
    BatchGenerateService,
    DBBatchPersistence,
)


@pytest.fixture
def file_db(tmp_path):
    """文件版 SQLite 引擎 + 会话工厂（WAL + busy_timeout，跨线程可见）。"""
    db_path = tmp_path / "batch_persist.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    Base.metadata.create_all(bind=eng)
    session_factory = sessionmaker(bind=eng, expire_on_commit=False)
    yield eng, session_factory
    eng.dispose()


def _ok(code_id=100, is_valid=True):
    return SimpleNamespace(code_id=code_id, is_valid=is_valid)


def _seed_project_cases(db_sess, project_id, case_ids):
    """插入一个 Project + 若干 TestCase（供 create_job 校验通过）；case_ids 显式给定，避免主键冲突。"""
    from app.models.project import Project
    from app.models.test_case import TestCase

    project = Project(id=project_id, name="p", target_url="https://x.com", status="active")
    db_sess.add(project)
    for cid in case_ids:
        db_sess.add(TestCase(
            id=cid, project_id=project_id, case_name=f"tc{cid}",
            case_no=f"TC{cid}", priority="P0", steps=json.dumps([{"step_number": 1}]),
        ))
    db_sess.commit()


def _wait_workers_done(svc, batch_id, timeout=15.0):
    """等待 worker 收敛（running==0 且全部退出），不做终态假设。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with svc._registry_lock:
            job = svc._jobs.get(batch_id)
        if job is not None:
            with job.lock:
                if job.running == 0 and len(job.workers) >= svc._max_workers \
                        and all(w["done"] for w in job.workers.values()):
                    return job
        time.sleep(0.02)
    raise TimeoutError("worker 未在期限内收敛")


# ═══════════════════════════════════════════════
# 1. batch_cases 逐行落库，字段与内存终态一致
# ═══════════════════════════════════════════════

def test_batch_cases_rows_match_memory_terminal(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")  # 真实模式，终端字段非 mock/kpi 生效
    persistence = DBBatchPersistence(sess_factory)
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(100 + cid, True),
        session_factory=sess_factory, persistence=persistence,
    )
    _seed_project_cases(sess_factory(), 1, [1, 2, 3])

    batch_id = svc.create_job(1, [1, 2, 3])
    summary = svc.wait_frozen(batch_id, timeout=20.0)
    assert summary["status"] == "completed"

    from app.models.batch_cases import BatchCase
    rows = sess_factory().query(BatchCase).filter(BatchCase.batch_id == batch_id).all()
    assert len(rows) == 3
    by_case = {r.case_id: r for r in rows}
    for cinfo in summary["cases"]:
        r = by_case[cinfo["case_id"]]
        assert r.status == cinfo["status"] == "success"
        assert r.code_id == cinfo["code_id"] == 100 + cinfo["case_id"]
        assert r.error_type is None
        assert r.is_valid_at_attempt == 1  # 真实 AI（非 mock）首次输出通过 Validator
        assert r.is_mock_at_attempt == 0
        assert r.kpi_eligible == 1
        assert r.attempt_count == 1
        assert r.terminal_at is not None


# ═══════════════════════════════════════════════
# 2. batch_records UNIQUE(batch_id)：重复写入幂等 UPSERT
# ═══════════════════════════════════════════════

def test_batch_records_upsert_idempotent(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    persistence = DBBatchPersistence(sess_factory)
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(),
        session_factory=sess_factory, persistence=persistence,
    )
    _seed_project_cases(sess_factory(), 1, [1])

    batch_id = svc.create_job(1, [1])
    summary = svc.wait_frozen(batch_id, timeout=20.0)
    assert summary["status"] == "completed"

    from app.models.batch_records import BatchRecord
    # 幂等 UPSERT：同一 batch_id 再次 finalize 不新增行，仅更新
    persistence.finalize(svc._jobs[batch_id], dict(summary, status="failed"))
    rows = sess_factory().query(BatchRecord).filter(BatchRecord.batch_id == batch_id).all()
    assert len(rows) == 1
    assert rows[0].batch_status == "failed"  # 被 UPSERT 更新


# ═══════════════════════════════════════════════
# 3. batch_records 写失败 → 不 completed，可重试且最终一致
# ═══════════════════════════════════════════════

class _FlakyFinalize(DBBatchPersistence):
    """在 finalize（batch_records 写）上注入失败指定次数的持久化实现"""

    def __init__(self, session_factory, fail_times):
        super().__init__(session_factory)
        self.fail_times = fail_times

    def finalize(self, job, summary):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("injected batch_records write failure")
        return super().finalize(job, summary)


def test_finalize_failure_retries_until_eventually_consistent(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    persistence = _FlakyFinalize(sess_factory, fail_times=2)
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(),
        session_factory=sess_factory, persistence=persistence,
        finalize_retries=5, finalize_backoff=0.02,
    )
    _seed_project_cases(sess_factory(), 1, [1])

    batch_id = svc.create_job(1, [1])
    summary = svc.wait_frozen(batch_id, timeout=30.0)  # 重试后最终一致
    assert summary["status"] == "completed"

    from app.models.batch_records import BatchRecord
    assert sess_factory().query(BatchRecord).filter(
        BatchRecord.batch_id == batch_id).count() == 1


def test_finalize_permanent_failure_never_enters_terminal(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    persistence = _FlakyFinalize(sess_factory, fail_times=999)
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(),
        session_factory=sess_factory, persistence=persistence,
        finalize_retries=2, finalize_backoff=0.0,
    )
    _seed_project_cases(sess_factory(), 1, [1])

    batch_id = svc.create_job(1, [1])
    job = _wait_workers_done(svc, batch_id)
    time.sleep(0.2)  # 让 supervisor 跑完 retry 并回退 running
    with job.lock:
        # 写失败不得进入任何终态：无终态快照、状态回退 running 而非 completed/failed
        assert job.terminal_snapshot is None
        assert job.status == "running"

    from app.models.batch_records import BatchRecord
    assert sess_factory().query(BatchRecord).filter(
        BatchRecord.batch_id == batch_id).count() == 0


# ═══════════════════════════════════════════════
# 4. commit 成功后崩溃 → 重启后 status 返回 task_lost 但能读到 batch_records
# ═══════════════════════════════════════════════

def test_restart_reads_batch_records_as_task_lost(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    persistence = DBBatchPersistence(sess_factory)
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(200 + cid, True),
        session_factory=sess_factory, persistence=persistence,
    )
    _seed_project_cases(sess_factory(), 1, [1, 2])

    batch_id = svc.create_job(1, [1, 2])
    summary = svc.wait_frozen(batch_id, timeout=20.0)
    assert summary["status"] == "completed"

    # 模拟重启：全新 Service 实例（空 _jobs/_tombstones），但同一 DB + 同一 persistence
    svc2 = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(),
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    st = svc2.status(1, batch_id)
    assert st["runtime_status"] == "task_lost"
    assert st["batch_id"] == batch_id
    assert st["success"] == 2
    # 逐 case 历史仍可从 batch_cases 读取
    from app.models.batch_cases import BatchCase
    assert sess_factory().query(BatchCase).filter(BatchCase.batch_id == batch_id).count() == 2


# ═══════════════════════════════════════════════
# 5. terminal 后修改 GeneratedCode.is_valid → batch_cases 行不变
# ═══════════════════════════════════════════════

def test_batch_cases_immutable_after_terminal(file_db, db_session, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    _seed_project_cases(sess_factory(), 1, [1])

    # 预置一个 GeneratedCode 行，fake result 引用其 code_id
    from app.models.generated_code import GeneratedCode
    gc = GeneratedCode(case_id=1, code_content="pass", is_valid=1)
    s = sess_factory()
    s.add(gc)
    s.commit()
    s.refresh(gc)
    gc_id = gc.id
    s.close()

    persistence = DBBatchPersistence(sess_factory)
    svc = BatchGenerateService(
        process_case=lambda pid, cid, sess, remaining=None: _ok(gc_id, True),
        session_factory=sess_factory, persistence=persistence,
    )
    batch_id = svc.create_job(1, [1])
    svc.wait_frozen(batch_id, timeout=20.0)

    from app.models.batch_cases import BatchCase
    s = sess_factory()
    bc = s.query(BatchCase).filter(BatchCase.batch_id == batch_id).one()
    assert bc.code_id == gc_id
    assert bc.is_valid_at_attempt == 1
    s.close()

    # terminal 后修改 GeneratedCode.is_valid（模拟 validate-on-load 变化）
    s = sess_factory()
    s.query(GeneratedCode).filter(GeneratedCode.id == gc_id).update({"is_valid": 0})
    s.commit()
    s.close()

    # batch_cases 行不变（冻结快照，不回写）
    s = sess_factory()
    bc_after = s.query(BatchCase).filter(BatchCase.batch_id == batch_id).one()
    assert bc_after.code_id == gc_id
    assert bc_after.is_valid_at_attempt == 1
    s.close()


# ═══════════════════════════════════════════════
# 6. 计数定义逐项验证（mock / deadline / slot_timeout 各一）
# ═══════════════════════════════════════════════

def test_kpi_bucket_counts_mock_deadline_slot_and_valid(file_db, mock_settings):
    eng, sess_factory = file_db

    # —— Batch A：Mock 模式（无 key）→ 单成功 case 也属 mock_excluded ——
    _seed_project_cases(sess_factory(), 1, [1])
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(),
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    bmock = svc.create_job(1, [1])
    sm = svc.wait_frozen(bmock, timeout=20.0)
    assert sm["status"] == "completed"
    assert sm["mock_excluded_count"] == 1
    assert sm["kpi_eligible_count"] == 0
    assert sm["processed_count"] == 1
    assert sm["ai_attempted_count"] == 0

    # —— Batch B：真实模式（注入 key）→ 混合 deadline/slot/pre_attempt/成功 ——
    mock_settings("OPENAI_API_KEY", "test-key")
    b_cids = [5, 6, 7, 8]  # 与 Batch A（project 1, cid 1）错开，避免 TestCase 主键冲突
    _seed_project_cases(sess_factory(), 2, b_cids)

    def process(pid, cid, s, remaining=None):
        if cid == 5:
            raise AIException("deadline", error_type="deadline_exceeded", retryable=False)
        if cid == 6:
            raise AIException("slot", error_type="slot_timeout", retryable=False)
        if cid == 7:
            return _ok(300 + cid, True)   # 首次真实 AI 输出通过 Validator
        return _ok(300 + cid, False)      # 首次输出未过 Validator（validation_failed）

    svc2 = BatchGenerateService(
        process_case=process,
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    breal = svc2.create_job(2, b_cids)
    sr = svc2.wait_frozen(breal, timeout=20.0)

    assert sr["requested_count"] == 4
    assert sr["processed_count"] == 4
    assert sr["ai_attempted_count"] == 3          # deadline + 2 成功；slot 未发 HTTP
    assert sr["deadline_excluded_count"] == 1
    assert sr["pre_attempt_excluded_count"] == 1  # slot_timeout
    assert sr["mock_excluded_count"] == 0
    assert sr["kpi_eligible_count"] == 2          # case7 + case8
    assert sr["first_gen_valid_count"] == 1       # case7
    assert sr["validation_failed_count"] == 1     # case8（首次输出未过 Validator）
    # 70% 首生成有效率口径 = sum(first_gen)/sum(kpi_eligible)
    assert sr["first_gen_valid_count"] / sr["kpi_eligible_count"] == 0.5

    # 逐 case exclusion_bucket 钉死
    by_case = {c["case_id"]: c for c in sr["cases"]}
    assert by_case[5]["exclusion_bucket"] == "deadline_excluded"
    assert by_case[6]["exclusion_bucket"] == "pre_attempt_excluded"
    assert by_case[7]["exclusion_bucket"] == "kpi_eligible"
    assert by_case[8]["exclusion_bucket"] == "kpi_eligible"
    assert by_case[8]["is_valid_at_attempt"] is False


# ═══════════════════════════════════════════════
# 7. 重启续跑：open job 重新建 + 跳过已终态 + 续跑未终态
# ═══════════════════════════════════════════════

def test_resume_open_job_skips_terminal_and_regenerates_rest(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    _seed_project_cases(sess_factory(), 1, [1, 2, 3])

    from app.models.batch_cases import BatchCase
    from app.models.batch_job import BatchJobModel
    from app.services.batch_generate_service import (
        BatchJob,
        BatchCaseSnapshot,
        DBBatchPersistence,
        BatchGenerateService,
    )

    # 模拟旧进程崩溃现场：batch_jobs 已写(running)，仅 case1 已终态落 batch_cases，
    # case2/3 仍是 pending（孤儿），且 batch_records 未收口。
    persistence = DBBatchPersistence(sess_factory)
    job = BatchJob("resume1", 1, [1, 2, 3])
    persistence.ensure_job_meta(job)
    snap = BatchCaseSnapshot(
        case_id=1, phase="terminal", status="success", code_id=901,
        is_valid_at_attempt=True, is_mock_at_attempt=False,
        attempt_count=1, latency_ms=10, kpi_eligible=True,
        exclusion_bucket="kpi_eligible",
    )
    persistence.persist_case(job, snap)

    # 重启：全新 Service 实例 + 同一 DB，对 case1 的 process_case 不可见（已被跳过）
    processed = []
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: (_ok(900 + cid, True)
                                                          if processed.append(cid) is None
                                                          else _ok()),
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    opened = svc.resume_open_jobs()
    assert opened == 1

    summary = svc.wait_frozen("resume1", timeout=20.0)
    assert summary["status"] == "completed"
    assert summary["success"] == 3
    assert sorted(c["case_id"] for c in summary["cases"]) == [1, 2, 3]
    # case1 由恢复快照计入（未重新生成）；case2/3 才是续跑生成
    assert sorted(processed) == [2, 3]

    rows = sess_factory().query(BatchCase).filter(BatchCase.batch_id == "resume1").all()
    assert len(rows) == 3
    bjm = sess_factory().query(BatchJobModel).filter(
        BatchJobModel.batch_id == "resume1").one()
    assert bjm.status == "completed"
    assert bjm.terminal_at is not None


def test_resume_no_open_jobs_returns_zero(file_db, mock_settings):
    eng, sess_factory = file_db
    mock_settings("OPENAI_API_KEY", "test-key")
    from app.services.batch_generate_service import DBBatchPersistence, BatchGenerateService
    svc = BatchGenerateService(
        process_case=lambda pid, cid, s, remaining=None: _ok(),
        session_factory=sess_factory, persistence=DBBatchPersistence(sess_factory),
    )
    assert svc.resume_open_jobs() == 0