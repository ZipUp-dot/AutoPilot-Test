"""BatchGenerateService 测试 — 双入口统一 / worker 并行消费 / 异常与 fatal / TTL 语义

对应任务 P0-1 验收标准：
  1. 两 worker 交错消费，最终 pending==0 && running==0，snapshot 冻结后不变
  2. worker 处理中抛异常：该 case 被同一 worker 置 failed(worker_failed)，running 归零，
     Batch 可正常 freeze（不悬挂）
  3. 一 worker 崩溃时另一 worker 在途 case 允许跑完，之后剩余 pending 变
     skipped(worker_failed)，BatchJob=failed
  4. create_job 重复 case_id 被拒；status project_id 不匹配 404
  5. TTL 清理后 task_expired；新进程查旧 Batch → task_lost；无历史 → 404
  6. orchestrator 与 router 双入口收敛到同一 Service 实例

本批测试使用 injection（fake process_case + fake session_factory），不开真实数据库。
"""

import threading
import time
from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundException, ValidationException
from app.services.batch_generate_service import (
    BatchGenerateService,
    InMemoryBatchPersistence,
)


class _WorkerCrash(Exception):
    """标记 Worker fatal 的测试异常（对应 fatal_error_types seam）"""


def _ok(cid: int):
    return SimpleNamespace(code_id=100 + cid, is_valid=True)


def build_service(
    case_ids,
    process_case=None,
    persistence=None,
    fatal_error_types=(),
    max_workers=2,
    ttl_seconds=24 * 3600,
):
    """构造 BatchGenerateService，注入 fake session（校验通过）与可选 process_case。

    默认使用 InMemoryBatchPersistence：本批测试聚焦 worker 并行消费 / 冻结 / fatal
    语义，不碰真实 DB。真实 batch_cases/batch_records 落库由 test_batch_persistence.py
    显式构造 DBBatchPersistence 验证。
    """
    session = _make_session(case_ids)

    def session_factory():
        return session

    return BatchGenerateService(
        process_case=process_case,
        session_factory=session_factory,
        persistence=persistence or InMemoryBatchPersistence(),
        max_workers=max_workers,
        ttl_seconds=ttl_seconds,
        fatal_error_types=fatal_error_types,
    )


def _make_session(case_ids):
    s = _FakeSession(case_ids)
    return s


class _FakeSession:
    """最小 fake：让 _validate_cases 的 project/case 查询都通过"""

    def __init__(self, case_ids):
        self.case_ids = case_ids

    def query(self, cls, *a, **k):
        res = _FakeQuery(self.case_ids)
        return res

    def close(self):
        pass


class _FakeQuery:
    def __init__(self, case_ids):
        self.case_ids = case_ids

    def filter(self, *a, **k):
        return self

    def first(self, *a, **k):
        # project 存在（truthy）
        return SimpleNamespace(id=1)

    def all(self, *a, **k):
        # 命中所有传入 case_ids，使校验通过
        return [(cid,) for cid in self.case_ids]


def _await_frozen(svc, batch_id):
    return svc.wait_frozen(batch_id, timeout=30.0)


# ═══════════════════════════════════════════════
# 1. 两 worker 交错消费，pending==0 && running==0，冻结后不变
# ═══════════════════════════════════════════════

def test_two_workers_interleave_and_freeze():
    case_ids = [1, 2, 3, 4, 5, 6]
    lock = threading.Lock()
    active = [0]
    max_active = [0]
    processed = []

    def process_case(pid, cid, session, remaining=None):
        with lock:
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
            processed.append(cid)
        return _ok(cid)

    svc = build_service(case_ids, process_case=process_case)
    batch_id = svc.create_job(1, case_ids)
    summary = _await_frozen(svc, batch_id)

    # 两 worker 确实并行（max_active>=2），且全部 case 都被处理
    assert max_active[0] >= 2, f"max_active={max_active[0]} 应>=2"
    assert sorted(processed) == case_ids

    # 终态：全 success，pending/running 归零
    assert summary["status"] == "completed"
    assert summary["success"] == 6
    assert summary["failed"] == 0
    assert sorted(c["case_id"] for c in summary["cases"]) == case_ids
    assert all(c["status"] == "success" for c in summary["cases"])

    # 冻结后不变：再次读取与第一次一致
    summary2 = _await_frozen(svc, batch_id)
    assert summary2["cases"] == summary["cases"]
    assert summary2["status"] == "completed"


# ═══════════════════════════════════════════════
# 2. 单 case 处理异常 → 同一 worker 置 failed(worker_failed)，不悬挂
# ═══════════════════════════════════════════════

def test_case_processing_exception_marks_failed_and_not_hang():
    case_ids = [1, 2, 3]

    def process_case(pid, cid, session, remaining=None):
        if cid == 2:
            raise RuntimeError("boom")
        return _ok(cid)

    svc = build_service(case_ids, process_case=process_case)
    batch_id = svc.create_job(1, case_ids)
    summary = _await_frozen(svc, batch_id)  # 不应悬挂

    by_id = {c["case_id"]: c for c in summary["cases"]}
    assert by_id[1]["status"] == "success"
    assert by_id[2]["status"] == "failed"
    assert by_id[2]["error_type"] == "worker_failed"
    assert by_id[3]["status"] == "success"
    # 存在失败 case → BatchJob=failed；但能正常 freeze（非 running）
    assert summary["status"] == "failed"
    assert summary["success"] == 2
    assert summary["failed"] == 1


# ═══════════════════════════════════════════════
# 3. 一 worker 崩溃 → 另一 worker 在途跑完，剩余 pending → skipped(worker_failed)
# ═══════════════════════════════════════════════

def test_worker_crash_aborts_inflight_completes_rest_skipped():
    case_ids = [1, 2, 3, 4]
    inflight_claimed = threading.Event()

    def process_case(pid, cid, session, remaining=None):
        if cid == 1:  # 崩溃 case：等 in-flight case 已 claim 后再崩，保证另一 worker 在途
            inflight_claimed.wait(timeout=5)
            raise _WorkerCrash("worker crash")
        if cid == 2:  # 在途 case：确保已被 claim，允许跑完
            inflight_claimed.set()
            time.sleep(0.2)
        return _ok(cid)

    svc = build_service(case_ids, process_case=process_case,
                        fatal_error_types=(_WorkerCrash,))
    batch_id = svc.create_job(1, case_ids)
    summary = _await_frozen(svc, batch_id)

    by_id = {c["case_id"]: c for c in summary["cases"]}
    # 崩溃 case：failed(worker_failed)
    assert by_id[1]["status"] == "failed"
    assert by_id[1]["error_type"] == "worker_failed"
    # 在途 case 允许跑完 → success
    assert by_id[2]["status"] == "success"
    # 剩余 pending → skipped(worker_failed)
    for cid in (3, 4):
        assert by_id[cid]["status"] == "skipped", f"case {cid} 应 skipped"
        assert by_id[cid]["error_type"] == "worker_failed"
    # 存在 skipped → BatchJob=failed
    assert summary["status"] == "failed"
    assert summary["skipped"] == 2


# ═══════════════════════════════════════════════
# 4. create_job 重复 case_id 被拒；status project 不匹配 → 404
# ═══════════════════════════════════════════════

def test_create_job_rejects_duplicate_case_ids():
    svc = build_service([1, 2])
    with pytest.raises(ValidationException, match="重复"):
        svc.create_job(1, [1, 1, 2])


def test_status_project_mismatch_404():
    svc = build_service([1], process_case=lambda pid, cid, s, remaining=None: _ok(cid))
    batch_id = svc.create_job(1, [1])
    _await_frozen(svc, batch_id)

    with pytest.raises(NotFoundException):
        svc.status(999, batch_id)
    # 正确 project 可查到
    st = svc.status(1, batch_id)
    assert st["batch_id"] == batch_id


# ═══════════════════════════════════════════════
# 5. TTL：task_expired / task_lost / 无历史 404
# ═══════════════════════════════════════════════

def test_ttl_expired_marks_task_expired_with_history():
    shared_persistence = InMemoryBatchPersistence()
    svc = build_service([1], process_case=lambda pid, cid, s, remaining=None: _ok(cid),
                        persistence=shared_persistence)
    batch_id = svc.create_job(1, [1])
    _await_frozen(svc, batch_id)

    # TTL 强制过期 → 清理运行态 Job + 记录 tombstone
    svc._ttl_seconds = -1  # noqa: SLF001 测试强制立即过期
    svc._evict_expired()

    # 内存 Job 已清，但 persistence 有历史 → task_expired
    st = svc.status(1, batch_id)
    assert st["runtime_status"] == "task_expired"
    assert st["batch_id"] == batch_id
    assert st["success"] == 1


def test_new_process_sees_task_lost_with_history():
    shared_persistence = InMemoryBatchPersistence()
    svc_a = build_service([1], process_case=lambda pid, cid, s, remaining=None: _ok(cid),
                          persistence=shared_persistence)
    batch_id = svc_a.create_job(1, [1])
    _await_frozen(svc_a, batch_id)

    # 模拟新进程：全新 Service 实例（空 _jobs/_tombstones），但共享同一 persistence
    svc_b = build_service([1], persistence=shared_persistence)
    st = svc_b.status(1, batch_id)
    assert st["runtime_status"] == "task_lost"
    assert st["success"] == 1


def test_no_history_returns_404():
    svc = build_service([1])
    with pytest.raises(NotFoundException):
        svc.status(1, "nonexistent")


# ═══════════════════════════════════════════════
# 6. orchestrator 与 router 双入口收敛到同一 Service
# ═══════════════════════════════════════════════

def test_router_and_orchestrator_share_singleton():
    from app.services import batch_generate_service as bgs_mod
    from app.routers.generate import batch_generate_service as router_svc
    from app.services.orchestrator import TestOrchestrator

    orch = TestOrchestrator()  # 未注入 → 用进程内单例
    assert router_svc is bgs_mod.batch_generate_service
    assert orch.batch_gen is bgs_mod.batch_generate_service
    # 双入口是同一实例
    assert orch.batch_gen is router_svc