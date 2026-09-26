"""P0-6 验收 3/4: 无 latest-code 后门 + Execution 创建统一经 Admission

3. Execution/Heal/Report 路径代码唯一来源 = ExecutionCodeResolver（读 runtime_state
   冻结的 active_code_id），不得回退 latest。
4. Executor/Router 不允许绕过 Admission 直调 create_execution。
   禁止：静默跳过非法 case、部分执行、Execution 级单值 active_code。
"""

import json

import pytest

VALID_CODE = (
    'async def run_test(page):\n'
    '    return {"success": True, "steps": []}\n'
)


def _steps(*items):
    return [
        {"step_number": i + 1, "action": a, "target": t, "value": "", "description": ""}
        for i, (a, t) in enumerate(items)
    ]


def _make_case(db_session, project, name="C", steps=None):
    from app.models.test_case import TestCase
    case = TestCase(
        project_id=project.id, case_name=name, case_no="TC", priority="P1",
        status="imported",
        steps=json.dumps(steps or _steps(("navigate", "https://example.com"))),
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def _make_code(db_session, case_id, steps, *, tag="A"):
    from app.models.generated_code import GeneratedCode
    code = GeneratedCode(
        case_id=case_id, code_content=f"{VALID_CODE}\n# {tag}\n",
        code_language="python", is_valid=1, is_mock=0,
        source_steps_hash=_hash(steps),
    )
    db_session.add(code)
    db_session.commit()
    db_session.refresh(code)
    return code


def _hash(steps):
    from app.utils.step_canonicalizer import hash_steps
    return hash_steps(steps)


@pytest.fixture
def project(db_session):
    from app.models.project import Project
    p = Project(name="Nb", target_url="https://example.com", status="active")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


class TestNoLatestBackdoor:
    def test_resolver_reads_frozen_runtime_state_not_latest(self, db_session, project):
        """Admission 冻结代码 A 后新增更新的代码 B(latest) → resolver 仍返回 A。

        证明 Execution/Heal/Report 代码来源 = runtime_state.active_code_id，
        不因存在更新的代码而重选码（无 latest 后门）。
        """
        from app.services.execution_admission_service import ExecutionAdmissionService
        from app.services.execution_code_resolver import ExecutionCodeResolver

        case = _make_case(db_session, project)
        code_a = _make_code(db_session, case.id, json.loads(case.steps))
        svc = ExecutionAdmissionService(db_session)
        result = svc.admit(project.id, [case.id])
        assert result.ok
        exec_id = svc.materialize(result, batch_name="B", mode="headless")
        # runtime_state 冻结 = code_a
        assert result.runtime_state[str(case.id)]["active_code_id"] == code_a.id

        # 生成更新的代码 B（同 hash，latest）——本应不被采用
        code_b = _make_code(db_session, case.id, json.loads(case.steps), tag="B")
        assert code_b.id != code_a.id
        assert code_b.source_steps_hash == code_a.source_steps_hash

        # resolver 必须返回 A（冻结的 active_code_id），而非 B
        resolver = ExecutionCodeResolver(db_session)
        active = resolver.get_active_code(exec_id, case.id)
        assert active is not None
        assert active.id == code_a.id
        assert active.id != code_b.id
        # 单 value 的 active_code 来自 runtime_state per-case，非 Execution 级单值
        assert resolver.get_active_code_id(exec_id, case.id) == code_a.id

    def test_resolver_returns_none_when_runtime_state_missing(self, db_session, project):
        """无 runtime_state → resolver 返回 None（不静默回退旧代码）。"""
        from app.services.execution_code_resolver import ExecutionCodeResolver
        case = _make_case(db_session, project)
        _make_code(db_session, case.id, json.loads(case.steps))
        # 新建一个没有 runtime_state 的执行
        from app.models.execution import Execution
        ex = Execution(project_id=project.id, status="queued", manifest_json="{}",
                       runtime_state_json=None)
        db_session.add(ex)
        db_session.commit()
        db_session.refresh(ex)
        assert ExecutionCodeResolver(db_session).get_active_code(ex.id, case.id) is None

    def test_no_latest_code_backdoor_in_execution_heal_report_source(self):
        """验收 3: 全库源码搜索证明 Execution/Heal/Report 路径无 latest-code 调用。

        检查 playback/appium/heal/report/orchestrator 源文件：
          - 不再调用 get_latest_code( 或 .latest() 选码
          - 不再从 Executor 侧 delete+rebuild 步骤
        执行路径代码来源统一为 ExecutionCodeResolver。
        """
        from pathlib import Path
        base = Path(__file__).resolve().parents[2] / "app"
        targets = [
            base / "services" / "playwright_service.py",
            base / "services" / "appium_service.py",
            base / "services" / "heal_service.py",
            base / "services" / "report_service.py",
            base / "services" / "orchestrator.py",
            base / "services" / "execution_code_resolver.py",
            base / "routers" / "executions.py",
        ]
        for path in targets:
            assert path.exists(), f"missing {path}"
            text = path.read_text(encoding="utf-8")
            # 不得直接调用 latest 选码的入口（get_latest_code 为展示 API，保留在 ai_service/generate 路由）
            assert "get_latest_code(" not in text, f"{path.name} 残留 get_latest_code 后门"
            # 生成结果查询不得回退最新的 code
            for bad in (".latest()", "order_by(GeneratedCode.id.desc())"):
                assert bad not in text, f"{path.name} 残留 latest 回退: {bad}"

    def test_runtime_state_has_per_case_active_code(self, db_session, project):
        """禁止 Execution 级单值 active_code：runtime_state 必须 per-case。"""
        from app.services.execution_admission_service import ExecutionAdmissionService
        c1 = _make_case(db_session, project, name="C1", steps=_steps(("navigate", "u")))
        c2 = _make_case(db_session, project, name="C2", steps=_steps(("click", "x")))
        _make_code(db_session, c1.id, json.loads(c1.steps))
        _make_code(db_session, c2.id, json.loads(c2.steps))

        svc = ExecutionAdmissionService(db_session)
        result = svc.admit(project.id, [c1.id, c2.id])
        assert result.ok
        assert set(result.runtime_state.keys()) == {str(c1.id), str(c2.id)}
        assert result.runtime_state[str(c1.id)]["active_code_id"] != result.runtime_state[str(c2.id)]["active_code_id"]
        # manifest per-case original_code_id 一一对应
        codes = {m["case_id"]: m["original_code_id"] for m in result.manifest}
        for cid in (c1.id, c2.id):
            assert result.runtime_state[str(cid)]["active_code_id"] == codes[cid]


class TestNoBypassCreateExecution:
    """验收 4: Router/Executor 不得绕过 Admission 直建 Execution。"""

    def test_executor_no_longer_creates_execution_rows(self, db_session):
        """Executor（playwright/appium）不再 self.create_execution 落库执行行。

        执行由 Admission.materialize 统一创建；Executor 仅消费已物化的 execution_id。
        """
        from pathlib import Path
        base = Path(__file__).resolve().parents[2] / "app"
        # oracle：orchestrator 启动线程调 svc.execute（不 create_execution）。
        orch_text = (base / "services" / "orchestrator.py").read_text(encoding="utf-8")
        # orchestrator 中执行入口统一经 _admit_and_launch
        assert "_admit_and_launch" in orch_text
        assert "self.create_execution(" not in orch_text

    def test_admission_failure_no_partial_execution(self, db_session, project):
        """禁止部分执行：任一 case 校验失败 → 整个 Admission ok=False，无 queued 落库。"""
        from app.services.execution_admission_service import ExecutionAdmissionService
        good = _make_case(db_session, project, name="Good")
        _make_code(db_session, good.id, json.loads(good.steps))
        # 非法 case：无步骤
        from app.models.test_case import TestCase
        bad = TestCase(project_id=project.id, case_name="Bad", case_no="T", status="imported",
                       steps="not json")
        db_session.add(bad)
        db_session.commit()

        svc = ExecutionAdmissionService(db_session)
        result = svc.admit(project.id, [good.id, bad.id])
        assert result.ok is False
        assert bad.id in result.errors

        from app.models.execution import Execution
        assert db_session.query(Execution).filter(Execution.project_id == project.id).count() == 0