"""P0-9 Stop 持久化 + Stop>Healing + Recovery 交互 —— 全场景测试

验收：
  1. running 态 Stop→DB 只有 stop_requested_at 变化，status 仍 running；
     case loop 结束后→stopped（六步收口事务）
  2. stop_requested=true 时 loop 结束不进入 healing
  3. Stop 后崩溃→Recovery 走 stopped 路径而非 interrupted（recovered_after_stop
     仅 error_type 层；无真实失败证据→terminal_reason=user_stopped；已有具体失败
     证据→保留原语义，Stop 不得覆盖）
  4. queued 态 Stop→立即 stopped（单事务六步：stop_requested_at + stopped +
     全部 Step→skipped(user_stopped) + open HealRound→cancelled + runtime_state 终态）
  5. 重复 Stop 幂等（stop_requested_at 不覆盖，状态不重复改写）

附加：
  - Worker 启动必须是条件更新（queued→running；影响行数 0 则放弃启动）
  - 决策点必须查 DB stop_requested_at，禁止只靠内存 flag
"""

import json
from datetime import datetime, timedelta

import pytest

from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.services.execution_state import recover_orphan_executions
from app.services.execution_finalizer import ExecutionFinalizer
from app.services.playwright_service import PlaywrightService


# ═══════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════

def _seed_execution(db_session, *, project, case_ids, status="queued",
                    stop_requested_at=None, steps_status="pending",
                    heartbeat=None, runtime_state_json=None):
    """创建 Execution + 每 case 一条指定状态 Step，返回 (exec_obj, steps)"""
    exec_obj = Execution(
        project_id=project.id,
        batch_name="StopSemantics",
        total_cases=len(case_ids),
        status=status,
        start_time=datetime.utcnow() - timedelta(minutes=5),
        heartbeat_at=heartbeat,
        stop_requested_at=stop_requested_at,
        runtime_state_json=runtime_state_json,
        # P1-1：_execute_async 只读 Manifest，_seed_execution 补冻结快照，
        # 否则裸 Execution target_url="" → validate 失败被 seal failed
        manifest_json=json.dumps({
            "target_url": project.target_url,
            "test_path": getattr(project, "test_path", "/") or "/",
            "browser_type": getattr(project, "browser_type", "chromium") or "chromium",
            "execution_mode": "headless",
            "ssrf_policy": {"allowed_hosts": [], "allowed_ports": []},
            "project": {
                "name": project.name,
                "target_url": project.target_url,
                "test_path": getattr(project, "test_path", "/") or "/",
            },
        }),
    )
    db_session.add(exec_obj)
    db_session.flush()
    steps = []
    for cid in case_ids:
        st = ExecutionStep(
            execution_id=exec_obj.id,
            case_id=cid,
            step_index=1,
            action="navigate",
            status=steps_status,
        )
        db_session.add(st)
        steps.append(st)
    db_session.commit()
    db_session.refresh(exec_obj)
    return exec_obj, steps


def _runtime(exec_obj):
    return json.loads(exec_obj.runtime_state_json or "{}")


# ═══════════════════════════════════════════════
# 验收 1：running 态 Stop 只写 stop_requested_at
# ═══════════════════════════════════════════════

class TestRunningStop:
    """验收 1/2/5：running 态 Stop 语义"""

    def test_running_stop_only_sets_stop_requested_at(
        self, client, db_session, sample_project, sample_test_case,
    ):
        """running 态 Stop → DB 只有 stop_requested_at 变化，status 仍 running"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
        )

        resp = client.post(f"/api/v1/executions/{exec_obj.id}/stop")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # running 态 Stop 不得立即置 stopped，只返回 {stop_requested: true}
        assert data["status"] == "running"
        assert data["stop_requested"] is True

        fresh = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert fresh.status == "running"
        assert fresh.stop_requested_at is not None
        assert fresh.end_time is None
        # Step 未被改写（等 loop 结束后由 Finalizer 统一收口）
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "pending"

    def test_loop_end_seals_stopped_six_steps(
        self, client, db_session, sample_project, sample_test_case,
    ):
        """running 态 Stop 后 loop 结束 → ExecutionFinalizer.seal_stopped 六步收口"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
        )
        client.post(f"/api/v1/executions/{exec_obj.id}/stop")

        # 执行器 loop 结束后走同一 Finalizer（唯一 stopped 出口）
        ExecutionFinalizer(db_session).seal_stopped(exec_obj.id)

        db_session.refresh(exec_obj)
        # ② status=stopped
        assert exec_obj.status == "stopped"
        assert exec_obj.end_time is not None
        # ① stop_requested_at（唯一权威字段）已写入
        assert exec_obj.stop_requested_at is not None
        # ③④ 全部 Step → skipped(user_stopped)
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "skipped"
        assert step.skip_reason == "user_stopped"
        # ⑥ runtime_state 写入最终 case_status / terminal_reason
        rs = _runtime(exec_obj)
        entry = rs[str(sample_test_case.id)]
        assert entry["case_status"] == "skipped"
        assert entry["terminal_reason"] == "user_stopped"


# ═══════════════════════════════════════════════
# 验收 4：queued 态 Stop 立即 stopped（六步收口事务）
# ═══════════════════════════════════════════════

class TestQueuedStop:
    """验收 4：queued 态 Stop → 立即 stopped"""

    def test_queued_stop_seals_immediately(
        self, client, db_session, sample_project, sample_test_case,
    ):
        """queued 态 Stop → 单事务六步：立即 stopped + 全部 Step→skipped(user_stopped)"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="queued",
        )

        resp = client.post(f"/api/v1/executions/{exec_obj.id}/stop")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "stopped"
        assert data["stop_requested"] is True

        db_session.refresh(exec_obj)
        assert exec_obj.status == "stopped"
        assert exec_obj.stop_requested_at is not None
        assert exec_obj.end_time is not None
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "skipped"
        assert step.skip_reason == "user_stopped"
        rs = _runtime(exec_obj)
        assert rs[str(sample_test_case.id)]["case_status"] == "skipped"
        assert rs[str(sample_test_case.id)]["terminal_reason"] == "user_stopped"


# ═══════════════════════════════════════════════
# 验收 5：重复 Stop 幂等
# ═══════════════════════════════════════════════

class TestRepeatStop:
    """验收 5：重复 Stop 幂等"""

    def test_repeat_stop_running_idempotent(
        self, client, db_session, sample_project, sample_test_case,
    ):
        """running 态重复 Stop → 第二次不覆盖 stop_requested_at，status 不变"""
        exec_obj, _ = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
        )

        client.post(f"/api/v1/executions/{exec_obj.id}/stop")
        first = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        t1 = first.stop_requested_at

        resp = client.post(f"/api/v1/executions/{exec_obj.id}/stop")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "running"
        assert data["stop_requested"] is True

        second = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert second.status == "running"
        # 幂等：stop_requested_at 不被覆盖（唯一权威字段保持原值）
        assert second.stop_requested_at == t1

    def test_repeat_stop_queued_after_sealed(
        self, client, db_session, sample_project, sample_test_case,
    ):
        """queued 已 stopped 后再 Stop → 幂等返回，不重复改写"""
        exec_obj, _ = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="queued",
        )
        client.post(f"/api/v1/executions/{exec_obj.id}/stop")

        resp = client.post(f"/api/v1/executions/{exec_obj.id}/stop")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "stopped"
        assert data["stop_requested"] is True

        fresh = db_session.query(Execution).filter(Execution.id == exec_obj.id).first()
        assert fresh.status == "stopped"
        # 终态不被再次改写（seal 幂等返回当前快照）
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "skipped"
        assert step.skip_reason == "user_stopped"


# ═══════════════════════════════════════════════
# 验收 3：Stop 后崩溃 → Recovery 走 stopped 路径
# ═══════════════════════════════════════════════

class TestRecoveryStopPath:
    """验收 3：stop_requested_at 非 NULL → Recovery 走 stopped 而非 interrupted"""

    def test_recovery_stop_in_flight_recovered_after_stop(
        self, db_session, sample_project, sample_test_case,
    ):
        """stop 后崩溃：在途 case→failed(recovered_after_stop)+user_stopped，
        剩余 pending→skipped(user_stopped)，Execution=stopped"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
            # 心跳新鲜也要走 stopped 路径（stop_requested 优先于一切状态判断）
            heartbeat=datetime.utcnow(),
        )
        # 在途 case：步骤正在执行（running）
        steps[0].status = "running"
        db_session.commit()

        # 模拟 Stop 已请求但未收敛即崩溃
        exec_obj.stop_requested_at = datetime.utcnow()
        db_session.commit()

        n = recover_orphan_executions(db_session)
        assert n == 1

        db_session.refresh(exec_obj)
        assert exec_obj.status == "stopped"

        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        # 在途 case：非终态步骤 → failed(recovered_after_stop)（仅 error_type 层）
        assert step.status == "failed"
        assert step.error_type == "recovered_after_stop"
        # 无真实业务失败 → terminal_reason=user_stopped（不得因崩溃改判 interrupted）
        rs = _runtime(exec_obj)
        assert rs[str(sample_test_case.id)]["case_status"] == "failed"
        assert rs[str(sample_test_case.id)]["terminal_reason"] == "user_stopped"

    def test_recovery_stop_remaining_pending_skipped(
        self, db_session, sample_project, sample_test_case,
    ):
        """stop 后崩溃：未执行的 pending case → skipped(user_stopped)"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
            steps_status="pending",
            heartbeat=datetime.utcnow(),
        )
        exec_obj.stop_requested_at = datetime.utcnow()
        db_session.commit()

        recover_orphan_executions(db_session)

        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "skipped"
        assert step.skip_reason == "user_stopped"

    def test_recovery_stop_preserves_real_failure_evidence(
        self, db_session, sample_project, sample_test_case,
    ):
        """在途 case 已有具体失败证据（business_failure）→ 保留原语义，Stop 不得覆盖"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
            heartbeat=datetime.utcnow(),
        )
        # 已有真实业务断言失败证据 + 一个在途步骤
        steps[0].status = "failed"
        steps[0].error_type = "business_assertion_failed"
        steps[0].error_message = "assert failed"
        db_session.add(ExecutionStep(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            step_index=2,
            action="click",
            status="running",
        ))
        db_session.commit()
        exec_obj.stop_requested_at = datetime.utcnow()
        db_session.commit()

        recover_orphan_executions(db_session)

        rs = _runtime(exec_obj)
        # 已有具体失败证据 → 保留真实语义（business_failure），Stop 不得覆盖为 user_stopped
        assert rs[str(sample_test_case.id)]["case_status"] == "failed"
        assert rs[str(sample_test_case.id)]["terminal_reason"] == "business_failure"

    def test_recovery_no_stop_still_interrupted(
        self, db_session, sample_project, sample_test_case,
    ):
        """无 Stop 意图 → Recovery 走原 interrupted 路径（Regression Guard）"""
        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
            heartbeat=datetime.utcnow() - timedelta(seconds=3600),
        )
        steps[0].status = "running"
        db_session.commit()

        n = recover_orphan_executions(db_session)
        assert n == 1
        db_session.refresh(exec_obj)
        assert exec_obj.status == "interrupted"
        assert exec_obj.stop_requested_at is None
        # 无 stop 意图：在途步骤由 seal() 收口为 failed(incomplete_execution)（中性证据）
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "failed"
        assert step.error_type == "incomplete_execution"


# ═══════════════════════════════════════════════
# 验收 2：stop_requested=true 时 loop 结束不进入 healing
# ═══════════════════════════════════════════════

class TestLoopEndStopPrecedence:
    """验收 2：case loop 结束后先查 stop_requested → 为真则不进入 healing"""

    @pytest.mark.asyncio
    async def test_stop_requested_loop_end_skips_healing(
        self, db_session, sample_project, sample_test_case, sample_generated_code,
        mock_playwright_for_execution_service, mocker,
    ):
        """Stop 到达后 loop 结束 → seal_stopped，即使存在失败也不进入 healing"""
        mocker.patch("app.utils.url_policy.install_network_policy", new=mocker.AsyncMock())

        exec_obj, steps = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="queued",
        )

        # 模拟用例执行中 Stop 到达：写 DB stop_requested_at（唯一权威）+ 用例失败
        async def fake_execute_case(page, execution_id, case_id):
            db_session.query(Execution).filter(
                Execution.id == execution_id
            ).update({"stop_requested_at": datetime.utcnow()})
            db_session.commit()
            return False  # any_failure = True，但 Stop 优先级更高

        healing_called = []
        mocker.patch.object(PlaywrightService, "_execute_case", side_effect=fake_execute_case)
        mocker.patch.object(
            PlaywrightService, "_start_healing",
            side_effect=lambda *a, **k: healing_called.append(1),
        )

        svc = PlaywrightService(db_session)
        await svc._execute_async(sample_project.id, [sample_test_case.id], exec_obj.id, "headless")

        db_session.refresh(exec_obj)
        # 不进入 healing；直接 stopped 收口
        assert healing_called == []
        assert exec_obj.status == "stopped"
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id
        ).first()
        assert step.status == "skipped"
        assert step.skip_reason == "user_stopped"


# ═══════════════════════════════════════════════
# 原子状态机 + DB 权威判定
# ═══════════════════════════════════════════════

class TestAtomicStateMachine:
    """Worker 启动条件更新 + 决策点查 DB（禁止只靠内存 flag）"""

    def test_conditional_start_queued_to_running(
        self, db_session, sample_project, sample_test_case,
    ):
        """queued → running（条件更新成功）；二次调用（已被改写）→ 放弃启动"""
        exec_obj, _ = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="queued",
        )
        svc = PlaywrightService(db_session)

        assert svc._mark_running_if_queued(exec_obj.id) is True
        db_session.refresh(exec_obj)
        assert exec_obj.status == "running"

        # 已被 Stop/Recovery 抢先改写（或本 worker 已启动）→ 影响行数 0，放弃启动
        assert svc._mark_running_if_queued(exec_obj.id) is False
        db_session.refresh(exec_obj)
        assert exec_obj.status == "running"  # 未回跳

    def test_conditional_start_on_terminal_returns_false(
        self, db_session, sample_project, sample_test_case,
    ):
        """已终态（stopped）→ 条件更新影响行数 0，杜绝 stopped→running 回跳"""
        exec_obj, _ = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="stopped",
        )
        svc = PlaywrightService(db_session)
        assert svc._mark_running_if_queued(exec_obj.id) is False
        db_session.refresh(exec_obj)
        assert exec_obj.status == "stopped"

    def test_stop_decision_queries_db_not_memory_flag(
        self, db_session, sample_project, sample_test_case,
    ):
        """决策点查 DB stop_requested_at（唯一权威），内存 flag 已清空仍判定 Stop"""
        # clear_global_state autouse 已清空 _stop_flags
        exec_obj, _ = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
            stop_requested_at=datetime.utcnow(),
        )
        assert PlaywrightService._stop_requested(db_session, exec_obj.id) is True

    def test_stop_decision_db_null_without_flag_returns_false(
        self, db_session, sample_project, sample_test_case,
    ):
        """DB stop_requested_at 为 NULL 且内存 flag 未设 → 未请求停止"""
        exec_obj, _ = _seed_execution(
            db_session, project=sample_project,
            case_ids=[sample_test_case.id], status="running",
        )
        assert PlaywrightService._stop_requested(db_session, exec_obj.id) is False
