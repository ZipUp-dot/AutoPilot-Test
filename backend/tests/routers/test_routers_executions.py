"""Router tests for execution endpoints — /api/v1/projects/{pid}/executions + /api/v1/executions/..."""

import pytest
from app.models.execution_step import ExecutionStep


def test_create_execution(client, sample_project, sample_test_case, sample_generated_code, mock_playwright_for_execution_service):
    """POST /api/v1/projects/{pid}/executions — returns 200 with execution_id"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "headless", "batch_name": "Test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "execution_id" in data["data"]


def test_execute_without_generated_code(client, sample_project, sample_test_case):
    """POST /api/v1/projects/{pid}/executions — no generated code returns 422"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "headless"},
    )
    assert resp.status_code == 422


def test_execute_invalid_mode(client, sample_project, sample_test_case, sample_generated_code):
    """POST /api/v1/projects/{pid}/executions — invalid mode returns 422"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "invalid"},
    )
    assert resp.status_code == 422


def test_list_executions(client, sample_project, sample_execution):
    """GET /api/v1/projects/{pid}/executions — returns list of executions"""
    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "items" in data["data"]
    assert len(data["data"]["items"]) >= 1
    assert data["data"]["total"] >= 1


# ═══════════════════════════════════════════════
# 列表实时聚合测试
# ═══════════════════════════════════════════════

def _create_execution_with_steps(db_session, project, steps_by_case, **overrides):
    """创建 Execution + 步骤记录的辅助函数

    steps_by_case: {case_id: [step_status, ...]}
    """
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep
    from datetime import datetime as dt

    total = sum(len(sts) > 0 for sts in steps_by_case.values())
    exec_obj = Execution(
        project_id=project.id,
        batch_name=overrides.pop("batch_name", "Realtime Batch"),
        total_cases=overrides.pop("total_cases", total),
        status=overrides.pop("status", "running"),
        start_time=dt.utcnow(),
        **overrides,
    )
    db_session.add(exec_obj)
    db_session.flush()
    for case_id, statuses in steps_by_case.items():
        for idx, st in enumerate(statuses, start=1):
            db_session.add(ExecutionStep(
                execution_id=exec_obj.id,
                case_id=case_id,
                step_index=idx,
                action="navigate",
                status=st,
                duration_ms=100,
            ))
    db_session.commit()
    db_session.refresh(exec_obj)
    return exec_obj


def _find_item(data, exec_id):
    """从列表接口响应中找到指定执行记录"""
    return next(i for i in data["data"]["items"] if i["id"] == exec_id)


def test_list_executions_realtime_aggregates_progress(client, db_session, sample_project, sample_test_case):
    """GET /api/v1/projects/{pid}/executions — progress 按实时聚合计算

    2 个用例：用例 A 全部 success → passed；用例 B 存在 failed → failed
    Execution 表缓存统计为 0/0，但实时聚合应反映步骤真实状态。
    """
    from app.models.test_case import TestCase
    case_b = TestCase(
        project_id=sample_project.id,
        case_name="Second Case",
        steps="[]",
        status="imported",
    )
    db_session.add(case_b)
    db_session.commit()
    db_session.refresh(case_b)

    exec_obj = _create_execution_with_steps(
        db_session, sample_project,
        steps_by_case={
            sample_test_case.id: ["success", "success"],
            case_b.id: ["failed"],
        },
        passed_cases=0,
        failed_cases=0,
    )

    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    item = _find_item(resp.json(), exec_obj.id)
    # 实时聚合覆盖缓存统计
    assert item["passed_cases"] == 1
    assert item["failed_cases"] == 1
    assert item["progress"] == 100


def test_list_executions_ignores_cached_stats(client, db_session, sample_project, sample_test_case):
    """实时聚合优先于 Execution 表缓存的 passed_cases/failed_cases"""
    exec_obj = _create_execution_with_steps(
        db_session, sample_project,
        steps_by_case={sample_test_case.id: ["failed"]},
        passed_cases=10,  # 缓存值故意不一致
        failed_cases=0,
    )

    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    item = _find_item(resp.json(), exec_obj.id)
    assert item["passed_cases"] == 0
    assert item["failed_cases"] == 1
    assert item["progress"] == 100


def test_list_executions_no_steps_fallback_to_cached(client, db_session, sample_project):
    """无步骤记录（刚创建）→ 回退使用缓存统计"""
    exec_obj = _create_execution_with_steps(
        db_session, sample_project,
        steps_by_case={},
        total_cases=3,
        passed_cases=2,
        failed_cases=1,
    )

    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    item = _find_item(resp.json(), exec_obj.id)
    assert item["passed_cases"] == 2
    assert item["failed_cases"] == 1
    assert item["progress"] == 100


def test_list_executions_progress_zero_when_total_zero(client, db_session, sample_project):
    """total_cases=0 → progress 为 0，避免除零"""
    exec_obj = _create_execution_with_steps(
        db_session, sample_project,
        steps_by_case={},
        total_cases=0,
        passed_cases=0,
        failed_cases=0,
    )

    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    item = _find_item(resp.json(), exec_obj.id)
    assert item["progress"] == 0


def test_list_executions_healing_mixed_steps_latest_record_wins(client, db_session, sample_project, sample_test_case):
    """healing 状态：用例经历 failed → success（自愈成功），最终状态取最新记录 → 计入 passed，而非 failed"""
    exec_obj = _create_execution_with_steps(
        db_session, sample_project,
        steps_by_case={sample_test_case.id: ["failed", "success"]},
        status="healing",
        passed_cases=1,
        failed_cases=0,
    )

    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    item = _find_item(resp.json(), exec_obj.id)
    # 最终状态 = 最新一条执行记录状态（success 覆盖之前的 failed）
    assert item["passed_cases"] == 1
    assert item["failed_cases"] == 0
    assert item["status"] == "healing"
    assert item["progress"] == 100


def test_list_executions_includes_progress_field(client, sample_project, sample_execution):
    """列表项包含 progress/execution_mode/duration 等字段"""
    resp = client.get(f"/api/v1/projects/{sample_project.id}/executions")
    assert resp.status_code == 200
    item = _find_item(resp.json(), sample_execution.id)
    assert "progress" in item
    assert "execution_mode" in item
    assert "duration" in item
    assert "platform" in item
    assert item["platform"] == "web"


def test_get_execution_detail(client, sample_execution):
    """GET /api/v1/executions/{eid} — returns 200 with steps and case_results"""
    resp = client.get(f"/api/v1/executions/{sample_execution.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "steps" in data["data"]
    assert len(data["data"]["steps"]) >= 1
    assert "case_results" in data["data"]
    assert len(data["data"]["case_results"]) >= 1
    # 验证 case_results 聚合正确：sample_execution 的步骤 status=success
    for cr in data["data"]["case_results"]:
        assert cr["status"] in ("success", "failed", "running", "pending", "skipped", "unknown")
        assert "case_name" in cr
        assert "step_count" in cr
        assert "duration" in cr
        assert "steps" in cr


def test_get_execution_detail_with_failed_case(client, db_session, sample_project, sample_test_case):
    """GET /api/v1/executions/{eid} — case_results 应包含 failed 状态"""
    from app.models.execution import Execution
    from app.models.execution_step import ExecutionStep
    from datetime import datetime as dt

    exec_obj = Execution(
        project_id=sample_project.id,
        batch_name="Failed Batch",
        total_cases=1,
        passed_cases=0,
        failed_cases=1,
        status="completed",
        start_time=dt.utcnow(),
        end_time=dt.utcnow(),
    )
    db_session.add(exec_obj)
    db_session.flush()
    step = ExecutionStep(
        execution_id=exec_obj.id,
        case_id=sample_test_case.id,
        step_index=1,
        action="click",
        status="failed",
        duration_ms=100,
    )
    db_session.add(step)
    db_session.commit()

    resp = client.get(f"/api/v1/executions/{exec_obj.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    cr = data["data"]["case_results"]
    assert len(cr) == 1
    assert cr[0]["status"] == "failed"
    assert cr[0]["case_name"] == sample_test_case.case_name
    assert cr[0]["step_count"] == 1


def test_get_execution_detail_nonexistent(client):
    """GET /api/v1/executions/{eid} — nonexistent returns 404"""
    resp = client.get("/api/v1/executions/99999")
    assert resp.status_code == 404


def test_get_execution_status(client, sample_execution):
    """GET /api/v1/executions/{eid}/status — returns progress data with percentage"""
    resp = client.get(f"/api/v1/executions/{sample_execution.id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "percentage" in data["data"]
    assert "progress" in data["data"]


def test_stop_execution(client, sample_project, sample_test_case, sample_generated_code, mock_playwright_for_execution_service):
    """POST /api/v1/executions/{eid}/stop — returns 200, status changes to stopped"""
    resp = client.post(
        f"/api/v1/projects/{sample_project.id}/executions",
        json={"case_ids": [sample_test_case.id], "mode": "headless", "batch_name": "StopTest"},
    )
    eid = resp.json()["data"]["execution_id"]

    resp = client.post(f"/api/v1/executions/{eid}/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["status"] == "stopped"


def test_stop_execution_already_completed(client, sample_execution):
    """POST /api/v1/executions/{eid}/stop — already completed returns message"""
    resp = client.post(f"/api/v1/executions/{sample_execution.id}/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["status"] == "completed"


def test_get_execution_status_nonexistent(client):
    """GET /api/v1/executions/{eid}/status — nonexistent returns 404"""
    resp = client.get("/api/v1/executions/99999/status")
    assert resp.status_code == 404


def test_stop_execution_nonexistent(client):
    """POST /api/v1/executions/{eid}/stop — nonexistent returns 404"""
    resp = client.post("/api/v1/executions/99999/stop")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════
# create_execution 边界
# ═══════════════════════════════════════════════

class TestCreateExecutionEdgeCases:
    """POST /api/v1/projects/{pid}/executions — 参数校验与异常包装"""

    def test_create_with_nonexistent_case(self, client, sample_project):
        """case_ids 含不存在用例且无代码 → 422，错误消息含 ID 回退"""
        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/executions",
            json={"case_ids": [99999], "mode": "headless"},
        )
        assert resp.status_code == 422
        assert "ID=99999" in resp.json()["message"]

    def test_create_orchestrator_error_wrapped(self, client, sample_project, sample_test_case, sample_generated_code, mocker):
        """run_execute_only 抛通用异常 → 包装为 422 '启动执行失败'"""
        mock_orch = mocker.MagicMock()
        mock_orch.run_execute_only.side_effect = RuntimeError("boom")
        mocker.patch("app.routers.executions.get_orchestrator", return_value=mock_orch)

        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/executions",
            json={"case_ids": [sample_test_case.id], "mode": "headless"},
        )
        assert resp.status_code == 422
        assert "启动执行失败" in resp.json()["message"]

    def test_create_case_without_code_uses_case_name(self, client, db_session, sample_project, sample_test_case):
        """存在用例但无生成代码 → 422，错误消息使用 case_name"""
        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/executions",
            json={"case_ids": [sample_test_case.id], "mode": "headless"},
        )
        assert resp.status_code == 422
        assert sample_test_case.case_name in resp.json()["message"]


# ═══════════════════════════════════════════════
# 跨项目保护（project_id 参数）
# ═══════════════════════════════════════════════

class TestCrossProjectProtection:
    """project_id 参数不匹配 → 404"""

    def test_detail_project_mismatch(self, client, sample_execution):
        resp = client.get(
            f"/api/v1/executions/{sample_execution.id}",
            params={"project_id": 99999},
        )
        assert resp.status_code == 404

    def test_status_project_mismatch(self, client, sample_execution):
        resp = client.get(
            f"/api/v1/executions/{sample_execution.id}/status",
            params={"project_id": 99999},
        )
        assert resp.status_code == 404

    def test_stop_project_mismatch(self, client, sample_execution):
        resp = client.post(
            f"/api/v1/executions/{sample_execution.id}/stop",
            params={"project_id": 99999},
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════
# 详情聚合状态分类
# ═══════════════════════════════════════════════

class TestDetailStatusClassification:
    """get_execution_detail() case_results 状态分类"""

    @pytest.mark.parametrize("step_status,expected", [
        ("running", "running"),
        ("pending", "pending"),
        ("skipped", "skipped"),
        ("weird_status", "unknown"),
    ])
    def test_detail_case_status(self, client, db_session, sample_project, sample_test_case, step_status, expected):
        exec_obj = _create_execution_with_steps(
            db_session, sample_project,
            steps_by_case={sample_test_case.id: [step_status]},
        )
        resp = client.get(f"/api/v1/executions/{exec_obj.id}")
        assert resp.status_code == 200
        cr = resp.json()["data"]["case_results"]
        assert cr[0]["status"] == expected


# ═══════════════════════════════════════════════
# 状态轮询：current_case 与截图
# ═══════════════════════════════════════════════

class TestStatusPollingDetail:
    """get_execution_status() current_case / latest_screenshot"""

    def test_status_returns_current_case(self, client, db_session, sample_project, sample_test_case):
        exec_obj = _create_execution_with_steps(
            db_session, sample_project,
            steps_by_case={sample_test_case.id: ["running"]},
        )
        resp = client.get(f"/api/v1/executions/{exec_obj.id}/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["current_case"] == sample_test_case.case_name

    def test_status_latest_screenshot_after(self, client, db_session, sample_project, sample_test_case):
        exec_obj = _create_execution_with_steps(
            db_session, sample_project,
            steps_by_case={sample_test_case.id: ["success"]},
        )
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
        ).first()
        step.screenshot_after = "uploads/screenshots/1/1/step_1_after.png"
        db_session.commit()

        resp = client.get(f"/api/v1/executions/{exec_obj.id}/status")
        data = resp.json()["data"]
        assert data["latest_screenshot"] == "uploads/screenshots/1/1/step_1_after.png"

    def test_status_latest_screenshot_fallback_before(self, client, db_session, sample_project, sample_test_case):
        exec_obj = _create_execution_with_steps(
            db_session, sample_project,
            steps_by_case={sample_test_case.id: ["success"]},
        )
        step = db_session.query(ExecutionStep).filter(
            ExecutionStep.execution_id == exec_obj.id,
        ).first()
        step.screenshot_before = "uploads/screenshots/1/1/step_1_before.png"
        db_session.commit()

        resp = client.get(f"/api/v1/executions/{exec_obj.id}/status")
        data = resp.json()["data"]
        assert data["latest_screenshot"] == "uploads/screenshots/1/1/step_1_before.png"