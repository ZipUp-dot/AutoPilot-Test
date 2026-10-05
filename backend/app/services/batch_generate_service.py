"""批量代码生成服务 — 双入口统一（生成页 UI 批量 / Orchestrator Full Pipeline）

架构（P0-1 施工解释）：
  - 进程内单例 BatchGenerateService。
  - 2 个 worker 线程（ThreadPoolExecutor(max_workers=2)）pull 消费：每次从 pending
    原子 claim 一个 case，处理完再取下一个，禁止一次性 submit 全部 case。
  - 每个 worker 线程内自建 SessionLocal（独立会话）。
  - 每个 worker 的 claim→process→terminalize 全生命周期由该 worker 自我包裹在
    try/except/finally 中：处理异常先由它把当前 case 置 failed(worker_failed)，
    finally 中 running-=1；Worker 的 terminalize 归属出错的 worker 本身，禁止
    Supervisor 越权去猜。
  - Supervisor 只负责：等待 worker 收敛 → 按序 Finalize（summary→persist→commit
    →freeze→status）。Worker fatal 时置 abort_requested（禁止新 claim），允许在途
    case 跑完，剩余 pending 由 Supervisor 统一收口为 skipped(worker_failed)。
  - TTL 只清理运行态内存 Job，绝不删除 Batch 历史事实；status 查询区分
    task_expired / task_lost / 404。
  - 正式 batch_cases/batch_records 持久化（P0-4）：默认使用 DBBatchPersistence。
    batch_cases 逐 Case 进入 terminal 时【同事务】写入且 terminal 后不可变；
    batch_records 在全部 Case terminal 后生成 summary → DB 事务写 → COMMIT 成功
    → 才进入终态 freeze。写失败不得进入任何终态（保持 running 可重试，最终一致）。
"""

import json
import logging
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from math import ceil
from typing import Any, Callable, Optional

from app.config import settings
from app.exceptions import AIException, NotFoundException, ValidationException

# 显式导入新 Model，确保导入本服务时即注册到 Base.metadata（测试 create_all 可建表）
from app.models.batch_cases import BatchCase  # noqa: F401
from app.models.batch_records import BatchRecord  # noqa: F401
from app.models.batch_job import BatchJobModel  # noqa: F401

# KPI exclusion bucket：priority=pre_attempt 的错误类型（未发生真实 HTTP attempt）


def _batch_budget_seconds(case_ids) -> float:
    """Batch 层预算：按用例数动态放大（整批 max(ceil(total/MAX_WORKERS) 轮 × 单 case 预算,
    且不低于 AI_BATCH_BUDGET_SECONDS)。与 BatchJob.__init__ 共用，启动续跑时重建同口径。"""
    rounds_needed = max(1, (len(case_ids) + MAX_WORKERS - 1) // MAX_WORKERS)
    return max(
        settings.AI_BATCH_BUDGET_SECONDS,
        rounds_needed * settings.AI_CASE_BUDGET_SECONDS,
    )

logger = logging.getLogger("autopilot.batch_generate")

# 并发 worker 数（pull 消费，禁止一次性 submit 全部 case）
MAX_WORKERS = 2
# 运行态内存 Job TTL：仅清理运行态，不删除历史事实
JOB_TTL_SECONDS = 24 * 3600

# Circuit：streak>=阈值 熔断（按 case 终态事件计，仅 non-mock）
CIRCUIT_THRESHOLD = 3

# KPI exclusion bucket：priority=pre_attempt 的错误类型（未发生真实 HTTP attempt）
PRE_ATTEMPT_ERROR_TYPES = frozenset({"quota_timeout", "slot_timeout", "pre_attempt"})


def _bool_or_none(v):
    """bool/None → 1/0/None（batch_cases is_valid_at_attempt 列）"""
    if v is None:
        return None
    return int(bool(v))


@dataclass
class BatchCaseSnapshot:
    """一次生成生命周期内单个 Case 的内存运行时/终态快照

    phase: pending / running / terminal
    status: success / failed / skipped（phase==terminal 时有效）

    is_valid_at_attempt / is_mock_at_attempt / attempt_count / kpi_eligible 为
    终态快照字段，terminal 时落定。kpi_eligible = 进入 70% 首生成有效率 cohort
    （真实 AI attempt && non-mock && 非 deadline 排除）。exclusion_bucket 为
    互斥 KPI 分桶（mock/deadline/pre_attempt/kpi_eligible），terminal 时冻结。
    processed = 是否被 claim 进入 running；ai_attempted = 是否≥1 次真实 HTTP。
    circuit_event = 本 case 终态产生的内部 Circuit 事件，Circuit 只消费该事件
    （禁止从 error_type 字符串反推）；claimed_at / case_deadline 承载 Case 层
    deadline（claim 成功后由 claim 时间 + AI_CASE_BUDGET_SECONDS 计算）。
    """

    case_id: int
    phase: str = "pending"
    status: str = "pending"
    code_id: Optional[int] = None
    is_valid_at_attempt: Optional[bool] = None
    is_mock_at_attempt: Optional[bool] = None
    error_type: Optional[str] = None
    attempt_count: int = 0
    latency_ms: int = 0
    kpi_eligible: bool = False
    exclusion_bucket: Optional[str] = None
    processed: bool = False
    ai_attempted: bool = False
    circuit_event: Optional[str] = None
    claimed_at: float = 0.0
    case_deadline: float = 0.0


class BatchJob:
    """运行态内存任务（进程重启即丢失，历史事实以 persistence 为准）"""

    def __init__(self, batch_id: str, project_id: int, case_ids: list[int]) -> None:
        self.batch_id = batch_id
        self.project_id = project_id
        self.total = len(case_ids)
        self.created_at = time.monotonic()
        # Batch 层 deadline：claim 前短路（batch_remaining = batch_deadline - now）。
        # 预算按用例数动态放大，保证整批（MAX_WORKERS 并行）能真实跑完而不被固定
        # 墙钟掐断：ceil(total/MAX_WORKERS) 轮 × 单 case 预算，且不低于 AI_BATCH_BUDGET_SECONDS。
        batch_budget = _batch_budget_seconds(case_ids)
        self.batch_deadline = self.created_at + batch_budget
        self.lock = threading.Lock()
        # 保序 dict：case_id -> BatchCaseSnapshot
        self.cases: dict[int, BatchCaseSnapshot] = {
            cid: BatchCaseSnapshot(case_id=cid) for cid in case_ids
        }
        self.running = 0
        self.success = 0
        self.failed = 0
        self.skipped = 0
        # worker 收敛登记：worker_name -> {done, fatal, reason}
        self.workers: dict[str, dict] = {}
        self.abort_requested = False
        self.status = "running"
        self.terminal_snapshot: Optional[dict] = None
        # Circuit 状态（按 case 终态事件计，仅 non-mock）
        self.streak = 0
        self.circuit_open = False
        # Batch deadline 到期标记（claim 前 batch_remaining<=0 置真）
        self.batch_expired = False


class BatchPersistence:
    """Batch 历史事实持久化抽象

    正式 batch_cases/batch_records 落库（P0-4）由 DBBatchPersistence 实现。
    status 查询的历史分支依赖本抽象，切换实现无需改调用方。
    """

    def finalize(self, job: BatchJob, summary: dict) -> None:
        """持久化终态 summary（DB 实现：写 batch_records + COMMIT；阻塞到成功）"""
        raise NotImplementedError

    def persist_case(self, job: BatchJob, snap: BatchCaseSnapshot, session=None) -> None:
        """逐 Case 终态写入（每个 BatchCase 进入 terminal 时同事务写入；此后不可变）。

        session 由 worker 传入实现「同事务」；为 None 时实现自建会话。
        默认 no-op（InMemory stub）；DB 实现负责 INSERT-only。
        """
        pass

    def has_history(self, batch_id: str) -> bool:
        raise NotImplementedError

    def load_history(self, batch_id: str) -> Optional[dict]:
        """返回历史 Batch 结果（含 project_id），无则 None"""
        raise NotImplementedError

    # ── 运行态元信息（服务重启续跑）──
    def ensure_job_meta(self, job) -> None:
        """创建 job 时持久化运行态元信息（batch_jobs 行，status=running）。"""
        raise NotImplementedError

    def mark_job_status(self, job, status: str, terminal: bool = False) -> None:
        """更新 batch_jobs 状态；terminal=True 时写 terminal_at。"""
        raise NotImplementedError

    def open_jobs(self) -> list:
        """扫描 status=running 的 open job，返回 [{batch_id, project_id, case_ids}]。"""
        raise NotImplementedError

    def terminal_cases(self, batch_id: str) -> dict:
        """返回该 batch 已终态化到 batch_cases 的 case_id -> 终态字段 dict。"""
        raise NotImplementedError


class InMemoryBatchPersistence(BatchPersistence):
    """内存 stub 持久化（测试用，不入真实 DB）"""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()

    def finalize(self, job: BatchJob, summary: dict) -> None:
        with self._lock:
            self._store[job.batch_id] = dict(summary)

    def has_history(self, batch_id: str) -> bool:
        with self._lock:
            return batch_id in self._store

    def load_history(self, batch_id: str) -> Optional[dict]:
        with self._lock:
            history = self._store.get(batch_id)
            return dict(history) if history else None

    # InMemory stub 不做运行态持久化；续跑仅对 DB 实现有意义
    def ensure_job_meta(self, job) -> None:
        pass

    def mark_job_status(self, job, status: str, terminal: bool = False) -> None:
        pass

    def open_jobs(self) -> list:
        return []

    def terminal_cases(self, batch_id: str) -> dict:
        return {}


class DBBatchPersistence(BatchPersistence):
    """真实 DB 持久化：batch_cases 逐 Case（INSERT-only，terminal 时冻结）+ batch_records summary

    - finalize(): batch_records 以 batch_id 唯一键 UPSERT（UNIQUE(batch_id)），
      保证重复写入幂等；COMMIT 成功才算持久化完成（调度顺序由此保证）。
    - persist_case(): 每个 BatchCase 进入 terminal 时写入 batch_cases 行；INSERT-only，
      terminal 后永不 UPDATE（后续 GeneratedCode/validate-on-load 变化不回写）。
    """

    def __init__(self, session_factory: Optional[Callable[[], Any]] = None) -> None:
        from app.db.database import SessionLocal

        self._session_factory = session_factory or SessionLocal

    def persist_case(self, job: BatchJob, snap: BatchCaseSnapshot, session=None) -> None:
        owns = session is None
        s = session or self._session_factory()
        try:
            s.add(BatchCase(
                project_id=job.project_id,
                batch_id=job.batch_id,
                case_id=snap.case_id,
                status=snap.status,
                code_id=snap.code_id,
                is_valid_at_attempt=_bool_or_none(snap.is_valid_at_attempt),
                is_mock_at_attempt=int(bool(snap.is_mock_at_attempt)),
                error_type=snap.error_type,
                attempt_count=snap.attempt_count,
                latency_ms=snap.latency_ms,
                kpi_eligible=int(bool(snap.kpi_eligible)),
            ))
            s.commit()
        finally:
            if owns:
                s.close()

    def finalize(self, job: BatchJob, summary: dict) -> None:
        s = self._session_factory()
        try:
            rec = s.query(BatchRecord).filter(BatchRecord.batch_id == job.batch_id).first()
            if rec is None:
                rec = BatchRecord(project_id=job.project_id, batch_id=job.batch_id)
                s.add(rec)
            rec.batch_status = summary["status"]
            rec.summary_json = json.dumps(summary, ensure_ascii=False)
            s.commit()
        finally:
            s.close()

    def has_history(self, batch_id: str) -> bool:
        s = self._session_factory()
        try:
            return s.query(BatchRecord).filter(BatchRecord.batch_id == batch_id).first() is not None
        finally:
            s.close()

    def load_history(self, batch_id: str) -> Optional[dict]:
        s = self._session_factory()
        try:
            rec = s.query(BatchRecord).filter(BatchRecord.batch_id == batch_id).first()
        finally:
            s.close()
        if rec is None:
            return None
        try:
            return json.loads(rec.summary_json)
        except (ValueError, TypeError):
            return None

    # ── 运行态元信息（服务重启续跑）──

    def ensure_job_meta(self, job) -> None:
        """创建 job 时写 batch_jobs(status=running)；已存在则幂等不覆盖。"""
        import datetime as _dt

        s = self._session_factory()
        try:
            exists = s.query(BatchJobModel).filter(BatchJobModel.batch_id == job.batch_id).first()
            if exists is None:
                s.add(BatchJobModel(
                    project_id=job.project_id,
                    batch_id=job.batch_id,
                    case_ids=json.dumps(list(job.cases.keys()), ensure_ascii=False),
                    status="running",
                    created_at=_dt.datetime.now(),
                    updated_at=_dt.datetime.now(),
                ))
                s.commit()
        finally:
            s.close()

    def mark_job_status(self, job, status: str, terminal: bool = False) -> None:
        """更新 batch_jobs 状态；terminal=True 时写 terminal_at。"""
        import datetime as _dt

        s = self._session_factory()
        try:
            rec = s.query(BatchJobModel).filter(BatchJobModel.batch_id == job.batch_id).first()
            if rec is not None:
                rec.status = status
                rec.updated_at = _dt.datetime.now()
                if terminal:
                    rec.terminal_at = _dt.datetime.now()
                s.commit()
        finally:
            s.close()

    def open_jobs(self) -> list:
        """扫描 status=running 的 open job（创建但未 finalize 的在途批次）。"""
        s = self._session_factory()
        try:
            rows = (
                s.query(BatchJobModel)
                .filter(BatchJobModel.status == "running")
                .all()
            )
        finally:
            s.close()
        out = []
        for r in rows:
            try:
                case_ids = json.loads(r.case_ids)
            except (ValueError, TypeError):
                case_ids = []
            out.append({
                "batch_id": r.batch_id,
                "project_id": r.project_id,
                "case_ids": case_ids,
            })
        return out

    def terminal_cases(self, batch_id: str) -> dict:
        """该 batch 已终态化到 batch_cases 的 case_id -> 终态字段 dict（供重启重建跳过）。"""
        s = self._session_factory()
        try:
            rows = (
                s.query(BatchCase)
                .filter(BatchCase.batch_id == batch_id)
                .all()
            )
        finally:
            s.close()
        return {
            r.case_id: {
                "status": r.status,
                "code_id": r.code_id,
                "is_valid_at_attempt": r.is_valid_at_attempt,
                "is_mock_at_attempt": bool(r.is_mock_at_attempt),
                "error_type": r.error_type,
                "attempt_count": r.attempt_count or 0,
                "latency_ms": r.latency_ms or 0,
                "kpi_eligible": bool(r.kpi_eligible),
            }
            for r in rows
        }


class BatchGenerateService:
    """批量代码生成服务（进程内单例，双入口收敛点）

    Args:
        process_case: 单 case 原语。默认走 AIService.generate_single；
            测试可注入 fake 以便验证并行消费/异常/fatal 等场景。
        session_factory: 每个 worker / create_job 校验自建会话的工厂。默认 SessionLocal。
        persistence: 历史事实持久化（默认内存 stub）。
        fatal_error_types: 视为 Worker fatal 的异常类型元组（测试 seam，默认空——
            真实运行中 generate_single 的任何异常都按单 case 失败处理，不触发 fatal）。
    """

    def __init__(
        self,
        process_case: Optional[Callable[[int, int, Any], Any]] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        persistence: Optional[BatchPersistence] = None,
        max_workers: int = MAX_WORKERS,
        ttl_seconds: float = JOB_TTL_SECONDS,
        fatal_error_types: tuple = (),
        finalize_retries: int = 5,
        finalize_backoff: float = 0.5,
    ) -> None:
        from app.db.database import SessionLocal

        self._process_case = process_case or self._default_process_case
        self._session_factory = session_factory or SessionLocal
        self._persistence = persistence or DBBatchPersistence(self._session_factory)
        self._max_workers = max_workers
        self._ttl_seconds = ttl_seconds
        self._fatal_error_types = tuple(fatal_error_types)
        self._finalize_retries = finalize_retries
        self._finalize_backoff = finalize_backoff

        self._jobs: dict[str, BatchJob] = {}
        self._tombstones: dict[str, float] = {}
        self._registry_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="batch-gen"
        )

    # ═══════════════════════════════════════════════
    # 单 case 原语
    # ═══════════════════════════════════════════════

    @staticmethod
    def _default_process_case(project_id: int, case_id: int, session: Any,
                              remaining: Optional[float] = None) -> Any:
        """默认单 case 原语：复用 AIService.generate_single，并把 case_remaining 预算
        贯穿进 AI attempt 链（quota/slot/HTTP/Retry-After/backoff，复用任务 3 的
        remaining 传播）。"""
        from app.services.ai_service import AIService

        return AIService(session).generate_single(
            project_id, case_id, remaining=remaining)

    # ═══════════════════════════════════════════════
    # 对外入口
    # ═══════════════════════════════════════════════

    def create_job(self, project_id: int, case_ids: list[int], db: Any = None) -> str:
        """创建批量生成任务并启动 worker + supervisor，返回 batch_id。

        Args:
            db: 可选的校验会话（router 传入 get_db 会话，使校验复用请求事务/测试 DB）；
                为 None 时用 self._session_factory 自建会话。
        """
        # 基础校验（纯逻辑，不碰 DB）
        if not case_ids:
            raise ValidationException("case_ids 不能为空")
        unique = list(dict.fromkeys(case_ids))
        if len(unique) != len(case_ids):
            raise ValidationException("case_ids 存在重复")

        # 校验 project 存在、全部 case 存在且属于该 project
        self._validate_cases(project_id, unique, db)

        batch_id = str(uuid.uuid4())[:8]
        job = BatchJob(batch_id, project_id, unique)
        with self._registry_lock:
            self._tombstones.pop(batch_id, None)
            self._jobs[batch_id] = job

        # 运行态元信息落库（重启续跑载体）；失败不阻断本次生成，仅日志告警
        try:
            self._persistence.ensure_job_meta(job)
        except Exception as e:  # noqa: BLE001
            logger.error("batch %s batch_jobs 元信息写失败（本次生成仍继续，但无法重启续跑）: %s",
                         batch_id, e)

        self._launch(job)
        return batch_id

    def status(self, project_id: int, batch_id: str) -> dict:
        """查询 batch 状态，返回实时/历史结果。不匹配或不存在 → NotFoundException。

        语义：
          - 内存 Job 存在 → 返回实时状态（未校验 project 会抛 404）。
          - 内存 Job 不存在：
              persistence 有历史 → 返回历史结果 + runtime_status
                  tombstone 命中 → task_expired（本进程 TTL 清理时记录）
                  无 tombstone → task_lost（含本进程启动前的旧 Job）
              persistence 无历史 → 404
        """
        self._evict_expired()
        with self._registry_lock:
            job = self._jobs.get(batch_id)

        if job is not None:
            if job.project_id != project_id:
                raise NotFoundException(f"批次 {batch_id} 不存在")
            return self._real_time(job)

        history = self._persistence.load_history(batch_id)
        if history is None:
            raise NotFoundException(f"批次 {batch_id} 不存在")
        if history.get("project_id") != project_id:
            raise NotFoundException(f"批次 {batch_id} 不存在")

        with self._registry_lock:
            tomb = batch_id in self._tombstones
        runtime_status = "task_expired" if tomb else "task_lost"
        return {**history, "runtime_status": runtime_status}

    def wait_frozen(self, batch_id: str, timeout: float = 600.0) -> dict:
        """同步等待 Batch 冻结（Orchestrator 路径使用，经 asyncio.to_thread 包装）

        Returns:
            frozen terminal snapshot (summary)
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._registry_lock:
                job = self._jobs.get(batch_id)
            if job is None:
                raise NotFoundException(f"批次 {batch_id} 不存在")
            with job.lock:
                if job.terminal_snapshot is not None:
                    return dict(job.terminal_snapshot)
            if time.monotonic() > deadline:
                raise TimeoutError(f"批次 {batch_id} 生成超时")
            time.sleep(0.02)

    # ═══════════════════════════════════════════════
    # 校验
    # ═══════════════════════════════════════════════

    def _validate_cases(self, project_id: int, case_ids: list[int], db: Any = None) -> None:
        from app.models.project import Project
        from app.models.test_case import TestCase

        owns_session = db is None
        session = db or self._session_factory()
        try:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise NotFoundException(f"项目 {project_id} 不存在")
            rows = (
                session.query(TestCase.id)
                .filter(TestCase.id.in_(case_ids), TestCase.project_id == project_id)
                .all()
            )
            found_ids = {r[0] for r in rows}
            missing = [c for c in case_ids if c not in found_ids]
            if missing:
                raise ValidationException(
                    f"用例不存在或不属于该项目: {missing}"
                )
        finally:
            if owns_session:
                session.close()

    # ═══════════════════════════════════════════════
    # 启动 worker + supervisor
    # ═══════════════════════════════════════════════

    def _launch(self, job: BatchJob) -> None:
        for i in range(self._max_workers):
            self._executor.submit(self._worker_loop, job, f"w{i}")
        self._executor.submit(self._supervisor, job)

    # ═══════════════════════════════════════════════
    # Worker：claim→process→terminalize 全生命周期自我包裹
    # ═══════════════════════════════════════════════

    def _claim(self, job: BatchJob) -> Optional[int]:
        """原子 pull-claim 一个 pending case。无 pending、熔断或 Batch 已到期 → None。

        claim 前短路（batch_remaining）：Circuit open 或 batch_deadline 已过时
        禁止再 claim，剩余 pending 由 Supervisor 统一收口为 skipped。
        claim 成功后创建 case_deadline = min(batch_deadline, claim_time + case_budget)。
        """
        with job.lock:
            if job.abort_requested or job.circuit_open:
                return None
            # batch_remaining 检查（claim 前，禁止混用 case_remaining 变量名）
            batch_remaining = job.batch_deadline - time.monotonic()
            if batch_remaining <= 0:
                job.batch_expired = True
                return None
            for snap in job.cases.values():
                if snap.phase == "pending":
                    snap.phase = "running"
                    snap.claimed_at = time.monotonic()
                    snap.case_deadline = min(
                        job.batch_deadline,
                        snap.claimed_at + settings.AI_CASE_BUDGET_SECONDS,
                    )
                    job.running += 1
                    return snap.case_id
            return None

    def _worker_loop(self, job: BatchJob, worker_name: str) -> None:
        with job.lock:
            job.workers[worker_name] = {"done": False, "fatal": False, "reason": None}

        session = None
        try:
            session = self._session_factory()
        except Exception as e:  # noqa: BLE001
            # Session 初始化失败 → Worker fatal（此刻未持有任何 case，合法）
            logger.error("batch %s worker %s 会话初始化失败（fatal）: %s",
                         job.batch_id, worker_name, e)
            self._report_worker(job, worker_name, fatal=True, reason="session_init_failed")
            return

        try:
            while True:
                case_id = self._claim(job)
                if case_id is None:
                    break
                # 若 fatal：_process 已在 finally 中 terminalize 当前 case，
                # 此时 worker 不再持有未 terminalize 的 case，报告 fatal 合法。
                if self._process(job, case_id, session):
                    self._report_worker(job, worker_name, fatal=True, reason="process_fatal")
                    return
        except Exception as e:  # noqa: BLE001
            # Worker 主循环自身无法继续（此刻未持有未 terminalize 的 case）→ fatal
            logger.exception("batch %s worker %s 主循环异常（fatal）", job.batch_id, worker_name)
            self._report_worker(job, worker_name, fatal=True, reason="worker_loop_error")
        finally:
            if session is not None:
                session.close()

        self._report_worker(job, worker_name, fatal=False)

    def _report_worker(self, job: BatchJob, worker_name: str, fatal: bool, reason: str = None) -> None:
        with job.lock:
            job.workers[worker_name] = {"done": True, "fatal": fatal, "reason": reason}
            if fatal:
                job.abort_requested = True  # 禁止新 claim，允许在途 case 跑完

    def _process(self, job: BatchJob, case_id: int, session: Any) -> bool:
        """处理单个 case。返回 True 表示 Worker fatal，False 表示正常（或单 case 失败）。

        任何处理异常都在本方法 finally 中由【当前 worker】自行 terminalize 当前 case，
        之后再决定是否上报 fatal；Supervisor 不接管已 claim 的 case。
        KPI exclusion bucket 依据 AIException.error_type 分类（deadline/slot/quota 等）。
        Circuit 只消费本方法产出的 circuit_event（以 retryable 标志为准，禁止从
        error_type 字符串反推）；Case deadline 在 claim 后立即检查、并把 case_remaining
        贯穿进 process_case，返回后做在途 overrun 判定。
        """
        from app.config import settings

        start = time.monotonic()
        snap = job.cases[case_id]
        # Case 层 deadline（claim 后）：case_remaining<=0 → 立即 deadline_exceeded
        case_remaining = snap.case_deadline - time.monotonic()
        if case_remaining <= 0:
            lat = int((time.monotonic() - start) * 1000)
            self._terminalize(
                job, case_id,
                status="failed", error_type="deadline_exceeded",
                is_mock_at_attempt=(not settings.OPENAI_API_KEY),
                attempt_count=0, latency_ms=lat,
                exclusion_bucket="deadline_excluded",
                circuit_event="non_retryable_failure", session=session,
            )
            return False

        try:
            result = self._process_case(job.project_id, case_id, session, case_remaining)
        except AIException as e:
            # 透传的 AIException：circuit_event 只从 retryable 标志计算
            fatal = isinstance(e, self._fatal_error_types) and not snap.phase == "terminal"
            is_mock = not settings.OPENAI_API_KEY
            bucket = self._classify_bucket(is_mock, e.error_type)
            # 可重试耗尽统一归 generation_failed（铁律 14），不再保留原始 HTTP error_type
            error_type = "generation_failed" if e.retryable else e.error_type
            circuit_event = "retryable_failure" if e.retryable else "non_retryable_failure"
            logger.warning("batch %s case %s 生成处理失败 error_type=%s retryable=%s: %s",
                           job.batch_id, case_id, e.error_type, e.retryable, str(e)[:200])
            latency = int((time.monotonic() - start) * 1000)
            self._terminalize(
                job, case_id,
                status="failed", error_type=error_type,
                is_mock_at_attempt=is_mock,
                attempt_count=0 if bucket == "pre_attempt_excluded" else (0 if is_mock else 1),
                latency_ms=latency,
                exclusion_bucket=bucket, circuit_event=circuit_event, session=session,
            )
            return fatal
        except Exception as e:  # noqa: BLE001
            fatal = isinstance(e, self._fatal_error_types) and not snap.phase == "terminal"
            error_type = "worker_failed"
            is_mock = not settings.OPENAI_API_KEY
            bucket = self._classify_bucket(is_mock, error_type)
            logger.warning("batch %s case %s 生成处理失败: %s",
                           job.batch_id, case_id, str(e)[:200])
            latency = int((time.monotonic() - start) * 1000)
            self._terminalize(
                job, case_id,
                status="failed", error_type=error_type,
                is_mock_at_attempt=is_mock,
                attempt_count=0 if is_mock else 1,
                latency_ms=latency,
                exclusion_bucket=bucket,
                circuit_event="non_retryable_failure", session=session,
            )
            return fatal

        is_mock = not settings.OPENAI_API_KEY
        is_valid = bool(getattr(result, "is_valid", False))
        code_id = getattr(result, "code_id", None)
        # 在途 overrun：返回时已越 case_deadline → 本次结果记 deadline_exceeded
        # （代码若有效仍由 generate_single 落库，供未来 Admission 复用，但不计本次 KPI）
        overrun = code_id is not None and time.monotonic() > snap.case_deadline
        if not overrun and code_id is not None and is_valid:
            status = "success"
            error_type = None
        elif overrun:
            status = "failed"
            error_type = "deadline_exceeded"
        else:
            # 有效代码但未过 Validator → validation_error（铁律 14，非 generation_failed）
            status = "failed"
            error_type = "validation_error" if code_id is not None else "generation_failed"
        bucket = "deadline_excluded" if overrun else self._classify_bucket(is_mock, error_type)
        circuit_event = "mock_success" if is_mock else ("success" if status == "success"
                                                        else "non_retryable_failure")
        latency = int((time.monotonic() - start) * 1000)
        self._terminalize(
            job, case_id,
            status=status,
            error_type=error_type,
            code_id=code_id,
            # is_valid_at_attempt：真实 AI（非 mock）首次输出是否通过 Validator。
            # generate_single 为单次输出，故近似等于本次 result.is_valid；mock 不填。
            is_valid_at_attempt=None if is_mock else bool(is_valid),
            is_mock_at_attempt=is_mock,
            attempt_count=0 if is_mock else 1,
            latency_ms=latency,
            exclusion_bucket=bucket, session=session,
        )
        return False

    @staticmethod
    def _classify_bucket(is_mock: bool, error_type: Optional[str]) -> str:
        """KPI exclusion 互斥分桶（一个 case 只进一个桶）：
        mock → mock_excluded；deadline → deadline_excluded；
        其余无真实 AI attempt（quota/slot 等 pre-HTTP）→ pre_attempt_excluded；
        真实 attempt + non-mock + 非 deadline → kpi_eligible
        """
        if is_mock:
            return "mock_excluded"
        if error_type == "deadline_exceeded":
            return "deadline_excluded"
        if error_type in PRE_ATTEMPT_ERROR_TYPES:
            return "pre_attempt_excluded"
        return "kpi_eligible"

    def _terminalize(
        self, job: BatchJob, case_id: int,
        status: str, error_type: Optional[str] = None,
        code_id: Optional[int] = None,
        is_valid_at_attempt: Optional[bool] = None,
        is_mock_at_attempt: Optional[bool] = None,
        attempt_count: int = 0, latency_ms: int = 0, kpi_eligible: bool = False,
        exclusion_bucket: Optional[str] = None, circuit_event: Optional[str] = None,
        session=None,
    ) -> None:
        was_running = False
        with job.lock:
            snap = job.cases[case_id]
            if snap.phase == "terminal":
                return  # 防重复收口
            was_running = snap.phase == "running"
            snap.phase = "terminal"
            snap.status = status
            snap.error_type = error_type
            snap.code_id = code_id
            snap.is_valid_at_attempt = is_valid_at_attempt
            snap.is_mock_at_attempt = is_mock_at_attempt
            snap.attempt_count = attempt_count
            snap.latency_ms = latency_ms
            snap.circuit_event = circuit_event
            if exclusion_bucket is not None:
                snap.exclusion_bucket = exclusion_bucket
                snap.kpi_eligible = (exclusion_bucket == "kpi_eligible")
            else:
                snap.exclusion_bucket = self._classify_bucket(
                    bool(is_mock_at_attempt), error_type)
                snap.kpi_eligible = kpi_eligible
            # Circuit：只消费内部 circuit_event，且仅 non-mock 计入（铁律 22）
            if not bool(snap.is_mock_at_attempt) and circuit_event is not None:
                if circuit_event == "success":
                    job.streak = 0
                elif circuit_event == "retryable_failure":
                    job.streak += 1
                # non_retryable_failure / mock_success / skipped → streak 不变
                if job.streak >= CIRCUIT_THRESHOLD:
                    job.circuit_open = True
            # processed = 曾进入 running；ai_attempted = 至少 1 次真实 HTTP
            snap.processed = was_running
            snap.ai_attempted = snap.exclusion_bucket in ("kpi_eligible", "deadline_excluded")
            if was_running:
                job.running -= 1
            if status == "success":
                job.success += 1
            elif status == "failed":
                job.failed += 1
            elif status == "skipped":
                job.skipped += 1
        # 内存快照冻结后（terminal 不可变）再落库 batch_cases，避免 lock 内做 DB
        self._persist_case(job, snap, session)

    def _persist_case(self, job: BatchJob, snap: BatchCaseSnapshot, session=None) -> None:
        """逐 Case batch_cases 持久化（终端时落库；失败仅记录，不阻塞 worker）"""
        try:
            self._persistence.persist_case(job, snap, session=session)
        except Exception as e:  # noqa: BLE001
            logger.error("batch %s case %s batch_cases 持久化失败: %s",
                         job.batch_id, snap.case_id, e)

    # ═══════════════════════════════════════════════
    # Supervisor：收敛等待 → 按序 Finalize
    # ═══════════════════════════════════════════════

    def _supervisor(self, job: BatchJob) -> None:
        try:
            self._wait_convergence(job)
            self._finalize(job)
        except Exception as e:  # noqa: BLE001
            logger.exception("batch %s supervisor 异常: %s", job.batch_id, e)

    def _all_workers_exited(self, job: BatchJob) -> bool:
        return len(job.workers) >= self._max_workers and all(
            w["done"] for w in job.workers.values()
        )

    def _wait_convergence(self, job: BatchJob) -> None:
        # 收敛必要条件：running==0 且全部 worker 已退出。worker 生命周期收敛是
        # 必要条件（否则最后一个 Case 完成瞬间另一 worker 尚在循环边界会提前收口）。
        while True:
            with job.lock:
                if job.terminal_snapshot is not None:
                    return
                if job.running == 0 and self._all_workers_exited(job):
                    return
            time.sleep(0.02)

    def _finalize(self, job: BatchJob) -> None:
        """Supervisor 收口（顺序钉死，completed 与 failed 同规则）：

        全部 Case terminal → 生成 summary → DB 事务写 batch_records → COMMIT 成功
        → BatchJob 进入终态并 freeze。区别仅终态值（正常收口 completed；存在
        worker/supervisor fatal 则 failed）。写失败不得进入任何终态：
        保持 running（可重试，最终一致）。
        """
        from app.config import settings

        skipped_snaps: list[BatchCaseSnapshot] = []
        with job.lock:
            if job.terminal_snapshot is not None or job.status != "running":
                return
            job.abort_requested = True  # seal：禁止新 claim
            # skip 原因：只允许 circuit_open / deadline_exceeded / worker_failed
            # （铁律 3：status=skipped 时 error_type 承载稳定跳过原因，作为 skip_reason）
            def _skip_reason():
                if job.circuit_open:
                    return "circuit_open"
                if job.batch_expired:
                    return "deadline_exceeded"
                return "worker_failed"

            skip_reason = _skip_reason()
            # Supervisor 只处理残留【pending】case（仅因 abort/circuit/deadline 产生）；绝不接管
            # 已 claim 的 running case（此处 running==0 保证无 in-flight）。
            for snap in job.cases.values():
                if snap.phase == "pending":
                    is_mock = not settings.OPENAI_API_KEY
                    if is_mock:
                        bucket = "mock_excluded"
                    elif skip_reason == "deadline_exceeded":
                        bucket = "deadline_excluded"
                    else:
                        bucket = "pre_attempt_excluded"
                    snap.phase = "terminal"
                    snap.status = "skipped"
                    snap.error_type = skip_reason
                    snap.is_valid_at_attempt = None
                    snap.is_mock_at_attempt = is_mock
                    snap.attempt_count = 0
                    snap.latency_ms = 0
                    snap.kpi_eligible = False
                    snap.exclusion_bucket = bucket
                    snap.circuit_event = "skipped"
                    snap.processed = False
                    snap.ai_attempted = False
                    job.skipped += 1
                    skipped_snaps.append(snap)
            summary = self._build_summary(job)
            job.status = "finalizing"  # 防并发 double-finalize

        # 逐个 skipped-pending 落 batch_cases（自建会话）
        for snap in skipped_snaps:
            self._persist_case(job, snap, session=None)

        # 顺序钉死：summary → persist(batch_records COMMIT) → freeze → status。
        if not self._persist_summary_with_retry(job, summary):
            # 写失败：回退为 running（可重试），不进入任何终态
            with job.lock:
                if job.status == "finalizing":
                    job.status = "running"
            return

        with job.lock:
            job.terminal_snapshot = summary
            job.status = summary["status"]
        try:
            self._persistence.mark_job_status(job, summary["status"], terminal=True)
        except Exception as e:  # noqa: BLE001
            logger.error("batch %s batch_jobs 终态写失败（batch_records 已落，历史仍可用）: %s",
                         job.batch_id, e)

    def _persist_summary_with_retry(self, job: BatchJob, summary: dict) -> bool:
        """持久化 batch_records 并阻塞到 COMMIT 成功；尾部重试，最终一致。失败返回 False。"""
        last_exc = None
        for attempt in range(1, self._finalize_retries + 1):
            try:
                self._persistence.finalize(job, summary)
                return True
            except Exception as e:  # noqa: BLE001
                last_exc = e
                logger.error("batch %s Finalization persist 第%d/共%d 次失败: %s",
                             job.batch_id, attempt, self._finalize_retries, e)
                if attempt < self._finalize_retries:
                    time.sleep(self._finalize_backoff * attempt)
        logger.error("batch %s Finalization persist 最终失败，BatchJob 保持 running（可重试）: %s",
                     job.batch_id, last_exc)
        return False

    def _build_summary(self, job: BatchJob) -> dict:
        cases = []
        kpi_counts = Counter()
        for cid, snap in job.cases.items():
            bucket = snap.exclusion_bucket or self._classify_bucket(
                bool(snap.is_mock_at_attempt), snap.error_type)
            kpi_counts[bucket] += 1
            if bucket == "kpi_eligible":
                # first_gen_valid = 首次真实 AI 输出通过 Validator；validation_failed =
                # 首次输出未过 Validator（后续 retry 成功不回写）
                if snap.is_valid_at_attempt is True:
                    kpi_counts["first_gen_valid"] += 1
                elif snap.is_valid_at_attempt is False:
                    kpi_counts["validation_failed"] += 1
            cases.append({
                "case_id": cid,
                "status": snap.status,
                "code_id": snap.code_id,
                "is_valid_at_attempt": snap.is_valid_at_attempt,
                "is_mock_at_attempt": snap.is_mock_at_attempt,
                "error_type": snap.error_type,
                "attempt_count": snap.attempt_count,
                "latency_ms": snap.latency_ms,
                "kpi_eligible": snap.kpi_eligible,
                "exclusion_bucket": bucket,
            })

        # 计数定义：processed=被 claim 进入 running；ai_attempted=≥1 次真实 HTTP
        processed_count = sum(1 for s in job.cases.values() if s.processed)
        ai_attempted_count = sum(1 for s in job.cases.values() if s.ai_attempted)

        # Batch 状态：存在失败/跳过 → failed；全部成功 → completed
        status = "completed" if (job.failed == 0 and job.skipped == 0) else "failed"
        return {
            # batch_records.summary_json 钉死 KPI 口径（首生成有效率）系列
            "schema_version": 1,
            "requested_count": job.total,
            "processed_count": processed_count,
            "ai_attempted_count": ai_attempted_count,
            "kpi_eligible_count": kpi_counts["kpi_eligible"],
            "first_gen_valid_count": kpi_counts["first_gen_valid"],
            "validation_failed_count": kpi_counts["validation_failed"],
            "deadline_excluded_count": kpi_counts["deadline_excluded"],
            "mock_excluded_count": kpi_counts["mock_excluded"],
            "pre_attempt_excluded_count": kpi_counts["pre_attempt_excluded"],
            # 扩展字段：保留既有 status 响应形状（含逐 Case 明细）
            "batch_id": job.batch_id,
            "project_id": job.project_id,
            "status": status,
            "total": job.total,
            "success": job.success,
            "failed": job.failed,
            "skipped": job.skipped,
            "cases": cases,
            "terminal_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ═══════════════════════════════════════════════
    # 实时状态 / 历史
    # ═══════════════════════════════════════════════

    def _real_time(self, job: BatchJob) -> dict:
        with job.lock:
            pending = running = success = failed = skipped = 0
            cases = []
            for cid, snap in job.cases.items():
                cases.append({
                    "case_id": cid,
                    "phase": snap.phase,
                    "status": snap.status if snap.phase == "terminal" else None,
                    "code_id": snap.code_id,
                    "is_valid_at_attempt": snap.is_valid_at_attempt,
                    "is_mock_at_attempt": snap.is_mock_at_attempt,
                    "error_type": snap.error_type,
                })
                if snap.phase == "pending":
                    pending += 1
                elif snap.phase == "running":
                    running += 1
                elif snap.status == "success":
                    success += 1
                elif snap.status == "failed":
                    failed += 1
                elif snap.status == "skipped":
                    skipped += 1
            terminal = job.terminal_snapshot is not None
            job_status = job.status if terminal else "running"
            completed = success + failed + skipped

        return {
            "batch_id": job.batch_id,
            "project_id": job.project_id,
            "status": job_status,
            "frozen": terminal,
            "total": job.total,
            "completed": completed,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "pending": pending,
            "running": running,
            "progress_pct": round(completed / job.total * 100, 1) if job.total > 0 else 0.0,
            "cases": cases,
        }

    # ═══════════════════════════════════════════════
    # TTL 清理
    # ═══════════════════════════════════════════════

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired_ids: list[str] = []
        with self._registry_lock:
            for bid, job in list(self._jobs.items()):
                if now - job.created_at > self._ttl_seconds:
                    expired_ids.append(bid)
            for bid in expired_ids:
                job = self._jobs.pop(bid)
                # 记录 expired tombstone（process-local）：查询命中 → task_expired
                self._tombstones[bid] = now
        if expired_ids:
            logger.info("批量生成运行态 Job TTL 清理: %s", expired_ids)

    def reset(self) -> None:
        """清空当前进程内运行态 Job 与 tombstone（测试隔离用，不影响 persistence 历史）"""
        with self._registry_lock:
            self._jobs.clear()
            self._tombstones.clear()

    # ═══════════════════════════════════════════════
    # 重启续跑（resume）：服务启动时重建未完成的批量生成
    # ═══════════════════════════════════════════════

    def resume_open_jobs(self) -> int:
        """启动时扫描持久化的 open job（status=running）并恢复续跑。

        每次扫描重建内存 BatchJob：
          - 已终态化进 batch_cases 的 case 重建为 terminal 快照（跳过，不重复生成）；
          - 未终态 case（pending/孤儿 running）重新投入 worker 消费；
          - 若全部 case 已 terminal 但尚未 finalize（旧进程死在收口前）→ 直接 finalize。
        返回本次恢复的 job 数量。已在内存的 job（id 重复）跳过，避免双启 worker。
        """
        opened = 0
        for meta in self._persistence.open_jobs():
            batch_id = meta["batch_id"]
            with self._registry_lock:
                if batch_id in self._jobs:
                    continue
            # 重建 BatchJob：fresh batch deadline（续跑给足预算），并恢复已终态快照
            job = BatchJob(batch_id, meta["project_id"], meta["case_ids"])
            terminal = self._persistence.terminal_cases(batch_id)
            self._restore_terminal(job, terminal)
            with self._registry_lock:
                self._jobs[batch_id] = job

            has_open = any(s.phase != "terminal" for s in job.cases.values())
            if has_open:
                self._launch(job)
            else:
                # 全部已终态但 batch_records / batch_jobs 尚未收口 → 直接 finalize
                try:
                    self._finalize(job)
                except Exception as e:  # noqa: BLE001
                    logger.exception("batch %s 续跑 finalize 异常: %s", batch_id, e)
            opened += 1
        return opened

    def _restore_terminal(self, job: BatchJob, terminal: dict) -> None:
        """把已终态化到 batch_cases 的 case 重建为 terminal 快照（跳过、计入汇总）。"""
        with job.lock:
            for cid, t in terminal.items():
                snap = job.cases.get(cid)
                if snap is None:
                    continue
                snap.phase = "terminal"
                snap.status = t["status"]
                snap.error_type = t.get("error_type")
                snap.code_id = t.get("code_id")
                snap.is_valid_at_attempt = t.get("is_valid_at_attempt")
                snap.is_mock_at_attempt = t.get("is_mock_at_attempt", False)
                snap.attempt_count = t.get("attempt_count", 0)
                snap.latency_ms = t.get("latency_ms", 0)
                snap.exclusion_bucket = self._classify_bucket(
                    t.get("is_mock_at_attempt", False), t.get("error_type"))
                snap.kpi_eligible = snap.exclusion_bucket == "kpi_eligible"
                snap.processed = True
                snap.ai_attempted = snap.exclusion_bucket in ("kpi_eligible", "deadline_excluded")
                if t["status"] == "success":
                    job.success += 1
                elif t["status"] == "failed":
                    job.failed += 1
                elif t["status"] == "skipped":
                    job.skipped += 1


# 进程内单例：router 与 orchestrator 双入口收敛到同一 Service 实例
batch_generate_service = BatchGenerateService()


# 便捷导出子类名（供 import/测试清晰引用持久化实现）
__all__ = [
    "BatchGenerateService",
    "BatchJob",
    "BatchCaseSnapshot",
    "BatchPersistence",
    "InMemoryBatchPersistence",
    "DBBatchPersistence",
    "batch_generate_service",
]