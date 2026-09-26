"""自愈路由测试 — POST manual heal + GET heal records"""

import pytest
from datetime import datetime as dt


class TestManualHeal:
    """POST /api/v1/executions/{eid}/heal"""

    def test_manual_heal(
        self,
        client,
        db_session,
        sample_project,
        sample_test_case,
        mock_playwright_for_heal_router,
        mocker,
    ):
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.services.heal_service import HealRoundResult

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.flush()
        step = ExecutionStep(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            step_index=1,
            action="click",
            status="failed",
        )
        db_session.add(step)
        db_session.commit()

        mock_result = HealRoundResult(
            heal_id=1,
            retry_status="success",
            error_type=None,
            healed_code="async def run_test(safe):\n    return {'success': True, 'steps': []}",
        )

        mocker.patch(
            "app.services.heal_service.HealRoundService.heal_case",
            new_callable=mocker.AsyncMock,
            return_value=mock_result,
        )

        resp = client.post(
            f"/api/v1/executions/{exec_obj.id}/heal",
            json={"case_id": sample_test_case.id, "step_index": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["heal_id"] == 1
        assert data["data"]["retry_status"] == "success"
        assert data["data"]["error_type"] is None
        assert "healed_code" in data["data"]

    def test_manual_heal_terminal_rejected(
        self,
        client,
        db_session,
        sample_project,
        sample_test_case,
        mocker,
    ):
        """验收 6：completed/stopped/failed/interrupted 四终态一律拒绝"""
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep

        for status in ("completed", "stopped", "failed", "interrupted"):
            exec_obj = Execution(
                project_id=sample_project.id,
                total_cases=1,
                status=status,
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
            )
            db_session.add(step)
            db_session.commit()

            resp = client.post(
                f"/api/v1/executions/{exec_obj.id}/heal",
                json={"case_id": sample_test_case.id, "step_index": 1},
            )
            assert resp.status_code == 422
            assert "终态" in resp.json()["message"]

    def test_manual_heal_step_not_found(
        self,
        client,
        db_session,
        sample_project,
        sample_test_case,
        mock_playwright_for_heal_router,
    ):
        from app.models.execution import Execution

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()

        resp = client.post(
            f"/api/v1/executions/{exec_obj.id}/heal",
            json={"case_id": sample_test_case.id, "step_index": 99},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "不存在" in data["message"]

    def test_manual_heal_step_not_failed(
        self,
        client,
        db_session,
        sample_project,
        sample_test_case,
        mock_playwright_for_heal_router,
    ):
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.flush()
        step = ExecutionStep(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            step_index=1,
            action="click",
            status="success",
        )
        db_session.add(step)
        db_session.commit()

        resp = client.post(
            f"/api/v1/executions/{exec_obj.id}/heal",
            json={"case_id": sample_test_case.id, "step_index": 1},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "非失败状态" in data["message"]


class TestHealRecords:
    """GET /api/v1/executions/{eid}/heal-records"""

    def test_get_heal_records(
        self,
        client,
        db_session,
        sample_project,
        sample_test_case,
    ):
        import json
        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.models.heal_record import HealRecord

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="completed",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.flush()
        step = ExecutionStep(
            execution_id=exec_obj.id,
            case_id=sample_test_case.id,
            step_index=1,
            action="click",
            status="failed",
        )
        db_session.add(step)
        db_session.flush()
        hr = HealRecord(
            execution_step_id=step.id,
            original_code="await page.click('#btn')",
            error_context=json.dumps({"error": "timeout"}),
            healed_code="await page.click('#new-btn')",
            heal_prompt="fix it",
            retry_status="success",
            retry_count=1,
        )
        db_session.add(hr)
        db_session.commit()

        resp = client.get(f"/api/v1/executions/{exec_obj.id}/heal-records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert "total" in data["data"]
        assert data["data"]["total"] == 1
        assert len(data["data"]["items"]) == 1
        item = data["data"]["items"][0]
        assert item["id"] == hr.id
        assert item["retry_status"] == "success"

    def test_get_heal_records_empty(
        self,
        client,
        db_session,
        sample_project,
    ):
        from app.models.execution import Execution

        exec_obj = Execution(
            project_id=sample_project.id,
            total_cases=1,
            status="running",
            start_time=dt.utcnow(),
        )
        db_session.add(exec_obj)
        db_session.commit()

        resp = client.get(f"/api/v1/executions/{exec_obj.id}/heal-records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 0
        assert data["data"]["items"] == []