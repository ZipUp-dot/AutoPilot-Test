import factory
from app.config import settings
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.element import PageElement
from app.models.generated_code import GeneratedCode
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.report import Report as ExecutionReport
from app.models.heal_record import HealRecord

__all__ = [
    "ProjectFactory", "TestCaseFactory", "PageElementFactory",
    "GeneratedCodeFactory", "ExecutionFactory", "ExecutionStepFactory",
    "ExecutionReportFactory", "HealRecordFactory",
]

class ProjectFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Project
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    name = factory.Sequence(lambda n: f"Project {n}")
    target_url = "https://example.com"
    test_path = "/"
    browser_type = "chromium"
    headless = True

class TestCaseFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = TestCase
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    project = factory.SubFactory(ProjectFactory)
    case_name = factory.Sequence(lambda n: f"Case {n}")
    case_no = factory.Sequence(lambda n: f"TC-{n:03d}")
    priority = "P1"
    steps = '[{"step_number":1,"action":"navigate","target":"https://example.com"}]'
    pre_condition = ""
    expected_result = "页面正常加载"
    status = "pending"

class PageElementFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PageElement
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    project = factory.SubFactory(ProjectFactory)
    element_type = "button"
    tag_name = "button"
    selector = factory.Sequence(lambda n: f"#btn-{n}")
    text_content = factory.Sequence(lambda n: f"Button {n}")

class GeneratedCodeFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = GeneratedCode
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    case = factory.SubFactory(TestCaseFactory)
    code_content = 'async def run_test(page): return {"success": True}'
    code_language = "python"
    is_valid = True
    ai_model = "gpt-4o"

class ExecutionFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Execution
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    project = factory.SubFactory(ProjectFactory)
    batch_name = factory.Sequence(lambda n: f"Batch {n}")
    status = "pending"
    total_cases = 0

class ExecutionStepFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ExecutionStep
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    execution = factory.SubFactory(ExecutionFactory)
    # 方案 A（推荐）：SubFactory 自动关联同一 Project
    case = factory.SubFactory(
        TestCaseFactory,
        project=factory.SelfAttribute("..execution.project")
    )
    # 备选方案（如果 ExecutionStep 模型只有 case_id 外键，无 case relationship）：
    # case_id = factory.LazyAttribute(lambda o: o.execution.project.test_cases[0].id)
    step_index = factory.Sequence(lambda n: n)
    action = "click"
    status = "pending"

class ExecutionReportFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ExecutionReport
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    execution = factory.SubFactory(ExecutionFactory)
    report_summary = '{"total":1,"passed":1,"failed":0}'
    download_url = factory.LazyAttribute(lambda o: f"{settings.REPORT_DIR}/test.html")

class HealRecordFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = HealRecord
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    execution_step = factory.SubFactory(ExecutionStepFactory)
    original_code = "await page.click('#btn')"
    # 统一引号风格：单引号外层 + 内部转义
    healed_code = 'await page.click(\'[data-testid="btn"]\')'
    retry_status = "success"
    retry_count = 1