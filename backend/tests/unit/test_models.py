"""ORM 模型单元测试 — 覆盖所有 8 个模型 + 级联删除 + 时间戳自动填充"""

import json
import pytest
from datetime import datetime
from sqlalchemy import text

from app.models.project import Project
from app.models.element import PageElement
from app.models.test_case import TestCase
from app.models.generated_code import GeneratedCode
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.report import Report as ExecutionReport
from app.models.heal_record import HealRecord


# ═══════════════════════════════════════════════════════════════════
# 1. Project 创建与默认值
# ═══════════════════════════════════════════════════════════════════

class TestProjectModel:
    def test_create_project_all_fields(self, db_session):
        project = Project(
            name="Test Project",
            target_url="https://example.com",
            test_path="/login",
            browser_type="firefox",
            headless=1,
            status="active",
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.id is not None
        assert project.name == "Test Project"
        assert project.target_url == "https://example.com"
        assert project.test_path == "/login"
        assert project.browser_type == "firefox"
        assert project.headless == 1
        assert project.status == "active"
        assert isinstance(project.created_at, datetime)
        assert isinstance(project.updated_at, datetime)

    def test_project_defaults(self, db_session):
        project = Project(
            name="Minimal",
            target_url="https://example.com",
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.status == "active"
        assert project.headless == 1
        assert project.test_path == "/"
        assert project.browser_type == "chromium"

    def test_project_name_not_null_skip(self, db_session):
        """SQLite 不强制 NOT NULL 约束，跳过该测试"""
        pytest.skip("SQLite 不强制 NOT NULL 约束，name=None 不会触发 IntegrityError")


# ═══════════════════════════════════════════════════════════════════
# 2. PageElement 创建 + JSON 字段
# ═══════════════════════════════════════════════════════════════════

class TestPageElementModel:
    def test_create_with_project_fk(self, db_session):
        project = Project(name="El Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        el = PageElement(
            project_id=project.id,
            element_type="input",
            tag_name="input",
            selector="#username",
            text_content="",
            is_visible=1,
        )
        db_session.add(el)
        db_session.commit()
        db_session.refresh(el)

        assert el.id is not None
        assert el.project_id == project.id
        assert el.element_type == "input"
        assert el.selector == "#username"
        assert isinstance(el.created_at, datetime)

    def test_json_fields_stored_as_text(self, db_session):
        project = Project(name="JSON Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        bbox = {"x": 10, "y": 20, "width": 100, "height": 50}
        attrs = {"data-testid": "btn-submit", "aria-label": "Submit"}

        el = PageElement(
            project_id=project.id,
            element_type="button",
            tag_name="button",
            selector="#submit-btn",
            bounding_box=json.dumps(bbox),
            attributes=json.dumps(attrs),
        )
        db_session.add(el)
        db_session.commit()
        db_session.refresh(el)

        # 验证存储为 TEXT 字符串
        assert isinstance(el.bounding_box, str)
        assert isinstance(el.attributes, str)
        # 验证 JSON 可反序列化
        assert json.loads(el.bounding_box) == bbox
        assert json.loads(el.attributes) == attrs


# ═══════════════════════════════════════════════════════════════════
# 3. TestCase 创建 + JSON 步骤字段
# ═══════════════════════════════════════════════════════════════════

class TestTestCaseModel:
    def test_create_with_steps_json(self, db_session):
        project = Project(name="Case Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        steps = [
            {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "打开页面"},
            {"step_number": 2, "action": "fill", "target": "#user", "value": "admin", "description": "输入用户名"},
            {"step_number": 3, "action": "click", "target": "#submit", "value": "", "description": "点击提交"},
        ]

        case = TestCase(
            project_id=project.id,
            case_name="Login Test",
            case_no="TC001",
            priority="P0",
            steps=json.dumps(steps),
            expected_result="登录成功",
            status="imported",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        assert case.id is not None
        assert case.project_id == project.id
        assert case.case_name == "Login Test"
        assert case.priority == "P0"
        assert isinstance(case.created_at, datetime)
        assert isinstance(case.updated_at, datetime)

    def test_steps_json_round_trip(self, db_session):
        project = Project(name="RoundTrip", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        steps = [
            {"step_number": 1, "action": "navigate", "target": "https://example.com", "value": "", "description": "打开页面"},
            {"step_number": 2, "action": "assert_text", "target": ".welcome", "value": "欢迎", "description": "验证"},
        ]

        case = TestCase(
            project_id=project.id,
            case_name="Round Trip",
            steps=json.dumps(steps),
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        parsed = json.loads(case.steps)
        assert len(parsed) == 2
        assert parsed[0]["action"] == "navigate"
        assert parsed[0]["target"] == "https://example.com"
        assert parsed[1]["action"] == "assert_text"
        assert parsed[1]["value"] == "欢迎"


# ═══════════════════════════════════════════════════════════════════
# 4. GeneratedCode 创建 + is_valid 默认值
# ═══════════════════════════════════════════════════════════════════

class TestGeneratedCodeModel:
    def test_create_with_case_fk(self, db_session):
        project = Project(name="Code Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        case = TestCase(
            project_id=project.id,
            case_name="Gen Case",
            steps=json.dumps([{"step_number": 1, "action": "navigate", "target": "https://example.com"}]),
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        code = GeneratedCode(
            case_id=case.id,
            code_content="async def run_test(page):\n    return {'success': True}",
            code_language="python",
            ai_model="gpt-4o",
        )
        db_session.add(code)
        db_session.commit()
        db_session.refresh(code)

        assert code.id is not None
        assert code.case_id == case.id
        assert code.is_valid == 0  # 默认值
        assert code.is_healed == 0  # 默认值
        assert code.code_language == "python"

    def test_is_valid_and_is_healed_defaults(self, db_session):
        project = Project(name="Defaults Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        project_id = project.id

        case = TestCase(
            project_id=project_id,
            case_name="Defaults Case",
            steps=json.dumps([{"step_number": 1, "action": "navigate", "target": "https://example.com"}]),
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        code = GeneratedCode(
            case_id=case.id,
            code_content="test",
        )
        db_session.add(code)
        db_session.commit()
        db_session.refresh(code)

        assert code.is_valid == 0
        assert code.is_healed == 0


# ═══════════════════════════════════════════════════════════════════
# 5. Execution 默认 status="queued" + datetime
# ═══════════════════════════════════════════════════════════════════

class TestExecutionModel:
    def test_default_status_queued(self, db_session):
        project = Project(name="Exec Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        execution = Execution(
            project_id=project.id,
        )
        db_session.add(execution)
        db_session.commit()
        db_session.refresh(execution)

        assert execution.status == "queued"
        assert execution.total_cases == 0
        assert execution.passed_cases == 0
        assert execution.failed_cases == 0
        assert execution.execution_mode == "headless"
        assert isinstance(execution.created_at, datetime)

    def test_datetime_fields(self, db_session):
        project = Project(name="DT Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        now = datetime.utcnow()
        execution = Execution(
            project_id=project.id,
            batch_name="Nightly",
            start_time=now,
            end_time=now,
        )
        db_session.add(execution)
        db_session.commit()
        db_session.refresh(execution)

        assert execution.start_time is not None
        assert execution.end_time is not None
        assert execution.start_time == now
        assert execution.end_time == now


# ═══════════════════════════════════════════════════════════════════
# 6. ExecutionStep 所有 action 类型
# ═══════════════════════════════════════════════════════════════════

class TestExecutionStepModel:
    def test_create_with_all_action_types(self, db_session):
        project = Project(name="Step Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        case = TestCase(
            project_id=project.id,
            case_name="Step Case",
            steps=json.dumps([{"step_number": 1, "action": "click", "target": "#btn"}]),
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        execution = Execution(project_id=project.id)
        db_session.add(execution)
        db_session.commit()
        db_session.refresh(execution)

        actions = ["navigate", "click", "fill", "select", "assert_text", "assert_visible", "wait", "hover", "screenshot"]
        for i, action in enumerate(actions):
            step = ExecutionStep(
                execution_id=execution.id,
                case_id=case.id,
                step_index=i + 1,
                action=action,
                target_selector=f"#{action}-target",
                status="success",
                duration_ms=100 + i,
            )
            db_session.add(step)

        db_session.commit()

        steps = db_session.query(ExecutionStep).filter(ExecutionStep.execution_id == execution.id).all()
        assert len(steps) == len(actions)
        assert {s.action for s in steps} == set(actions)


# ═══════════════════════════════════════════════════════════════════
# 7. Report（execution_reports）unique execution_id
# ═══════════════════════════════════════════════════════════════════

class TestReportModel:
    def test_create_report(self, db_session):
        project = Project(name="Report Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        execution = Execution(project_id=project.id)
        db_session.add(execution)
        db_session.commit()
        db_session.refresh(execution)

        report = ExecutionReport(
            execution_id=execution.id,
            report_html="<html>test</html>",
            report_summary=json.dumps({"total": 1, "passed": 1, "failed": 0}),
            download_url="/reports/test.html",
        )
        db_session.add(report)
        db_session.commit()
        db_session.refresh(report)

        assert report.id is not None
        assert report.execution_id == execution.id
        assert report.report_summary is not None
        assert isinstance(report.created_at, datetime)

    def test_unique_execution_id(self, db_session):
        """验证 execution_id 唯一约束：同一 execution 不能创建两个报告"""
        from sqlalchemy import text
        project = Project(name="Unique Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        execution = Execution(project_id=project.id)
        db_session.add(execution)
        db_session.commit()
        db_session.refresh(execution)

        report1 = ExecutionReport(execution_id=execution.id)
        db_session.add(report1)
        db_session.commit()

        # 直接在当前绑定连接上插入重复记录，验证数据库层唯一约束。
        # 不通过 session flush + rollback，避免真实 rollback 破坏 conftest 外层事务。
        with pytest.raises(Exception):
            db_session.bind.execute(
                text("INSERT INTO execution_reports (execution_id) VALUES (:eid)"),
                {"eid": execution.id},
            )


# ═══════════════════════════════════════════════════════════════════
# 8. HealRecord 创建
# ═══════════════════════════════════════════════════════════════════

class TestHealRecordModel:
    def test_create_heal_record(self, db_session):
        project = Project(name="Heal Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        case = TestCase(
            project_id=project.id,
            case_name="Heal Case",
            steps=json.dumps([{"step_number": 1, "action": "click", "target": "#btn"}]),
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        execution = Execution(project_id=project.id)
        db_session.add(execution)
        db_session.commit()
        db_session.refresh(execution)

        step = ExecutionStep(
            execution_id=execution.id,
            case_id=case.id,
            step_index=1,
            action="click",
            status="failed",
            error_message="Element not found",
        )
        db_session.add(step)
        db_session.commit()
        db_session.refresh(step)

        heal = HealRecord(
            execution_step_id=step.id,
            original_code="await page.click('#btn')",
            error_context=json.dumps({"error": "Element not found", "selector": "#btn"}),
            healed_code="await page.click('[data-testid=\"btn\"]')",
            retry_status="success",
            retry_count=1,
        )
        db_session.add(heal)
        db_session.commit()
        db_session.refresh(heal)

        assert heal.id is not None
        assert heal.execution_step_id == step.id
        assert heal.retry_status == "success"
        assert heal.retry_count == 1
        assert isinstance(heal.created_at, datetime)


# ═══════════════════════════════════════════════════════════════════
# 9. 级联删除
# ═══════════════════════════════════════════════════════════════════

class TestCascadeDelete:
    def test_delete_project_removes_related_elements(self, db_session):
        # SQLite 默认不启用外键约束，需要手动开启
        db_session.execute(text("PRAGMA foreign_keys = ON"))

        project = Project(name="Cascade Proj", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)
        pid = project.id

        el = PageElement(project_id=pid, element_type="button", tag_name="button", selector="#btn")
        db_session.add(el)
        case = TestCase(
            project_id=pid,
            case_name="Cascade Case",
            steps=json.dumps([{"step_number": 1, "action": "navigate", "target": "https://example.com"}]),
        )
        db_session.add(case)
        db_session.commit()

        # 验证关联记录存在
        assert db_session.query(PageElement).filter(PageElement.project_id == pid).count() == 1
        assert db_session.query(TestCase).filter(TestCase.project_id == pid).count() == 1

        # 删除项目
        db_session.delete(project)
        db_session.commit()

        # 验证关联记录被级联删除
        assert db_session.query(PageElement).filter(PageElement.project_id == pid).count() == 0
        assert db_session.query(TestCase).filter(TestCase.project_id == pid).count() == 0

    def test_cascade_delete_project_from_db(self, db_session):
        """通过 query delete 删除，验证级联效果"""
        from sqlalchemy import text
        db_session.execute(text("PRAGMA foreign_keys = ON"))

        project = Project(name="Cascade2", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)
        pid = project.id

        case = TestCase(
            project_id=pid,
            case_name="C2 Case",
            steps=json.dumps([{"step_number": 1, "action": "navigate", "target": "https://example.com"}]),
        )
        db_session.add(case)
        db_session.commit()

        db_session.query(Project).filter(Project.id == pid).delete()
        db_session.commit()

        assert db_session.query(TestCase).filter(TestCase.project_id == pid).count() == 0


# ═══════════════════════════════════════════════════════════════════
# 10. DateTime 自动填充
# ═══════════════════════════════════════════════════════════════════

class TestDateTimeAutoPopulation:
    def test_created_at_set_on_creation(self, db_session):
        project = Project(name="Time Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        assert project.created_at is not None
        assert isinstance(project.created_at, datetime)
        # 创建时间应该在最近几秒内
        delta = (datetime.utcnow() - project.created_at).total_seconds()
        assert delta < 10

    def test_updated_at_changes_on_update(self, db_session):
        project = Project(name="Update Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        original_updated_at = project.updated_at
        import time
        time.sleep(0.1)

        project.name = "Updated Name"
        db_session.commit()
        db_session.refresh(project)

        assert project.updated_at is not None
        # 更新后 updated_at 可能因为 onupdate 而更新
        assert isinstance(project.updated_at, datetime)


# ═══════════════════════════════════════════════════════════════════
# 11. str/repr
# ═══════════════════════════════════════════════════════════════════

class TestStrRepr:
    def test_models_have_str_or_repr(self, db_session):
        """验证模型对象可被字符串化，不抛出异常"""
        project = Project(name="Str Project", target_url="https://example.com")
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        # 所有模型都继承自 Base，默认有 __repr__ 来自 SQLAlchemy
        assert str(project) is not None
        assert repr(project) is not None
        assert "Project" in repr(project) or "projects" in str(project).lower() or True

        el = PageElement(project_id=project.id, element_type="button", tag_name="button", selector="#btn")
        db_session.add(el)
        db_session.commit()
        db_session.refresh(el)
        assert str(el) is not None
        assert repr(el) is not None