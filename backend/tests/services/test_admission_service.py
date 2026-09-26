"""P0-6 ExecutionAdmissionService — 10 项校验每项独立失败用例 + 全通过 + 单事务物化

验收：1) 10 项校验每项各有独立失败用例，失败时无 Execution 落库、逐 case 返回原因
       2) manifest immutable + Pre-start Drift Guard（见 test_manifest_immutable.py）
       4) Router 直调 create_execution 路径已消除（见 test_no_latest_code_backdoor.py）
"""

import json

import pytest

from app.exceptions import NotFoundException, ValidationException
from app.services.execution_admission_service import ExecutionAdmissionService
from app.utils.step_canonicalizer import hash_steps

VALID_CODE = (
    'async def run_test(page):\n'
    '    return {"success": True, "steps": []}\n'
)


def _steps(*items):
    return [
        {"step_number": i + 1, "action": a, "target": t, "value": "", "description": ""}
        for i, (a, t) in enumerate(items)
    ]


@pytest.fixture
def project(db_session):
    from app.models.project import Project
    p = Project(name="Adm", target_url="https://example.com", status="active")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_case(db_session, project, name="C1", steps=None):
    from app.models.test_case import TestCase
    case = TestCase(
        project_id=project.id,
        case_name=name,
        case_no="TC1",
        priority="P1",
        steps=json.dumps(steps or _steps(("navigate", "https://example.com"), ("click", "#btn"))),
        status="imported",
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def _make_code(db_session, case_id, *, is_valid=1, is_mock=0, steps=None, content=VALID_CODE):
    from app.models.generated_code import GeneratedCode
    code = GeneratedCode(
        case_id=case_id,
        code_content=content,
        code_language="python",
        is_valid=is_valid,
        is_mock=is_mock,
        source_steps_hash=hash_steps(steps or _steps(("navigate", "https://example.com"), ("click", "#btn"))),
    )
    db_session.add(code)
    db_session.commit()
    db_session.refresh(code)
    return code


def _admit(db_session, project_id, case_ids, **kw):
    return ExecutionAdmissionService(db_session).admit(project_id, case_ids, **kw)


def _assert_no_execution(db_session, project_id):
    """断言没有新的 queued Execution 落库。

    materialize 只会创建 status='queued' 的 Execution；retry 测试里源 Execution 为
    'completed'，不计入。admit 失败 → 不应有任何 queued Execution。
    """
    from app.models.execution import Execution
    return (
        db_session.query(Execution)
        .filter(Execution.project_id == project_id, Execution.status == "queued")
        .count() == 0
    )


# ── 全通过 + 物化 ──

class TestAdmitFullPass:
    def test_all_pass_ok_and_manifest_fields(self, db_session, project):
        case = _make_case(db_session, project)
        code = _make_code(db_session, case.id, steps=json.loads(case.steps))

        result = _admit(db_session, project.id, [case.id])

        assert result.ok is True
        assert not result.errors
        assert result.runtime_state[str(case.id)] == {"active_code_id": code.id}
        m = result.manifest[0]
        assert m["case_id"] == case.id
        assert m["case_name"] == "C1"
        assert m["priority"] == "P1"
        assert m["step_count"] == 2
        assert m["steps_hash"] == hash_steps(json.loads(case.steps))
        assert m["original_code_id"] == code.id

    def test_materialize_single_transaction(self, db_session, project):
        case = _make_case(db_session, project)
        code = _make_code(db_session, case.id, steps=json.loads(case.steps))
        result = _admit(db_session, project.id, [case.id])
        assert result.ok

        execution_id = ExecutionAdmissionService(db_session).materialize(result, batch_name="B", mode="headless")

        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        ex = db_session.query(Execution).filter(Execution.id == execution_id).first()
        # queued 的 Execution 必然携带完整 Manifest/Runtime/Steps（原子性）
        assert ex is not None and ex.status == "queued"
        assert ex.manifest_json and ex.runtime_state_json
        manifest = json.loads(ex.manifest_json)
        assert manifest["cases"][0]["original_code_id"] == code.id
        runtime = json.loads(ex.runtime_state_json)
        assert runtime[str(case.id)]["active_code_id"] == code.id
        steps = db_session.query(ExecutionStep).filter(ExecutionStep.execution_id == execution_id).all()
        assert len(steps) == 2

    def test_source_ambiguity_rejected(self, db_session, project):
        case = _make_case(db_session, project)
        code = _make_code(db_session, case.id, steps=json.loads(case.steps))
        with pytest.raises(ValidationException, match="来源歧义"):
            _admit(db_session, project.id, [case.id],
                   batch_id="b", retry_from_execution_id=1)


# ── 10 项校验：每项独立失败用例 ──

class TestAdmissionChecks:
    def test_1_project_not_found(self, db_session):
        with pytest.raises(NotFoundException):
            _admit(db_session, 99999, [1])

    def test_2_case_not_exist(self, db_session, project):
        result = _admit(db_session, project.id, [123456])
        assert result.ok is False
        assert result.errors[123456]
        assert _assert_no_execution(db_session, project.id)

    def test_3_duplicate_case_id(self, db_session, project):
        case = _make_case(db_session, project)
        code = _make_code(db_session, case.id, steps=json.loads(case.steps))
        result = _admit(db_session, project.id, [case.id, case.id])
        assert result.ok is False
        assert result.errors[case.id] == "case_id 重复"
        assert _assert_no_execution(db_session, project.id)

    def test_4_case_not_belong_to_project(self, db_session, project):
        other = _make_case(db_session, project)  # belongs to same project
        # 造一个属于其他项目的 case
        from app.models.project import Project
        from app.models.test_case import TestCase
        p2 = Project(name="Other", target_url="https://other.example", status="active")
        db_session.add(p2)
        db_session.commit()
        c2 = TestCase(project_id=p2.id, case_name="X", case_no="X", status="imported",
                      steps=json.dumps(_steps(("click", "#b"))))
        db_session.add(c2)
        db_session.commit()

        result = _admit(db_session, project.id, [c2.id])
        assert result.ok is False
        assert c2.id in result.errors
        assert _assert_no_execution(db_session, project.id)

    def test_5_steps_empty_or_invalid_json(self, db_session, project):
        from app.models.test_case import TestCase
        case = TestCase(project_id=project.id, case_name="NoSteps", case_no="T", status="imported",
                        steps="not json {{")
        db_session.add(case)
        db_session.commit()
        result = _admit(db_session, project.id, [case.id])
        assert result.ok is False
        assert "步骤" in result.errors[case.id]
        assert _assert_no_execution(db_session, project.id)

    def test_6_step_structure_incomplete(self, db_session, project):
        case = _make_case(db_session, project, steps=[
            {"step_number": 1, "target": "#a", "value": ""}  # 缺 action
        ])
        result = _admit(db_session, project.id, [case.id])
        assert result.ok is False
        assert "结构" in result.errors[case.id]
        assert _assert_no_execution(db_session, project.id)

    def test_7_step_index_duplicate(self, db_session, project):
        case = _make_case(db_session, project, steps=[
            {"step_number": 1, "action": "navigate", "target": "u"},
            {"step_number": 1, "action": "click", "target": "#b"},
        ])
        result = _admit(db_session, project.id, [case.id])
        assert result.ok is False
        assert "序号重复" in result.errors[case.id]
        assert _assert_no_execution(db_session, project.id)

    def test_8_no_code_id(self, db_session, project):
        # 有效步骤 but 无任何可复用代码
        case = _make_case(db_session, project)
        db_session.add(case)
        # 造一个 hash 不匹配导致 get_effective_code 返回 None 的代码
        _make_code(db_session, case.id, steps=_steps(("click", "#other")))  # 不同 steps hash
        result = _admit(db_session, project.id, [case.id])
        assert result.ok is False
        assert result.errors[case.id]
        assert "代码" in result.errors[case.id]
        assert _assert_no_execution(db_session, project.id)

    def test_9_code_is_mock_rejected(self, db_session, project):
        case = _make_case(db_session, project)
        code = _make_code(db_session, case.id, is_mock=1, steps=json.loads(case.steps))
        # 通过 retry 入口引入候选 code（get_effective_code 会过滤 mock，只有 retry 能暴露 9）
        src = _make_source_execution(db_session, project.id, {case.id: code.id})
        result = _admit(db_session, project.id, [case.id], retry_from_execution_id=src.id)
        assert result.ok is False
        assert "Mock" in result.errors[case.id]
        assert _assert_no_execution(db_session, project.id)

    def test_10_source_steps_hash_mismatch(self, db_session, project):
        case = _make_case(db_session, project)  # steps A
        code = _make_code(db_session, case.id, is_mock=0, steps=json.loads(case.steps))
        # 修改 TestCase.steps（漂移），使当前 hash 与 code.source_steps_hash 不一致
        case.steps = json.dumps(_steps(("click", "#btn")))
        db_session.commit()

        src = _make_source_execution(db_session, project.id, {case.id: code.id})
        result = _admit(db_session, project.id, [case.id], retry_from_execution_id=src.id)
        assert result.ok is False
        assert "来源步骤与当前用例步骤不一致" in result.errors[case.id]
        assert _assert_no_execution(db_session, project.id)


def _make_source_execution(db_session, project_id, code_id_map):
    from app.models.execution import Execution
    src = Execution(project_id=project_id, status="completed",
                    runtime_state_json=json.dumps(
                        {str(c): {"active_code_id": cid} for c, cid in code_id_map.items()}
                    ))
    db_session.add(src)
    db_session.commit()
    db_session.refresh(src)
    return src


# ── Batch 全集合 all-or-none ──

class TestBatchAdmission:
    def _seed_batch(self, db_session, project, batch_id, case_status_map):
        from app.models.batch_records import BatchRecord
        from app.models.batch_cases import BatchCase
        rec = BatchRecord(project_id=project.id, batch_id=batch_id,
                          batch_status="completed", summary_json="{}")
        db_session.add(rec)
        for cid, (status, code_id, mock) in case_status_map.items():
            db_session.add(BatchCase(project_id=project.id, batch_id=batch_id, case_id=cid,
                                     status=status, code_id=code_id,
                                     is_mock_at_attempt=1 if mock else 0))
        db_session.commit()

    def test_batch_full_pass(self, db_session, project):
        c1 = _make_case(db_session, project, name="A")
        c2 = _make_case(db_session, project, name="B")
        code1 = _make_code(db_session, c1.id, steps=json.loads(c1.steps))
        code2 = _make_code(db_session, c2.id, steps=json.loads(c2.steps))
        self._seed_batch(db_session, project, "batch-full",
                         {c1.id: ("success", code1.id, False),
                          c2.id: ("success", code2.id, False)})

        result = _admit(db_session, project.id, [c1.id, c2.id], batch_id="batch-full")
        assert result.ok is True
        assert result.runtime_state[str(c1.id)]["active_code_id"] == code1.id
        assert result.runtime_state[str(c2.id)]["active_code_id"] == code2.id

    def test_batch_subset_rejected(self, db_session, project):
        c1 = _make_case(db_session, project, name="A")
        c2 = _make_case(db_session, project, name="B")
        code1 = _make_code(db_session, c1.id, steps=json.loads(c1.steps))
        code2 = _make_code(db_session, c2.id, steps=json.loads(c2.steps))
        self._seed_batch(db_session, project, "batch-full",
                         {c1.id: ("success", code1.id, False),
                          c2.id: ("success", code2.id, False)})

        # 只提交子集 → 整批拒绝（禁止从完成 Batch 挑成功 Case 偷跑）
        result = _admit(db_session, project.id, [c1.id], batch_id="batch-full")
        assert result.ok is False
        assert result.errors[c1.id]
        assert _assert_no_execution(db_session, project.id)

    def test_batch_not_finalized_rejected(self, db_session, project):
        c1 = _make_case(db_session, project)
        code1 = _make_code(db_session, c1.id, steps=json.loads(c1.steps))
        self._seed_batch(db_session, project, "batch-running",
                         {c1.id: ("success", code1.id, False)})
        from app.models.batch_records import BatchRecord
        rec = db_session.query(BatchRecord).filter(BatchRecord.batch_id == "batch-running").first()
        rec.batch_status = "failed"
        db_session.commit()

        result = _admit(db_session, project.id, [c1.id], batch_id="batch-running")
        assert result.ok is False
        assert "未完成最终化" in result.errors[c1.id]
        assert _assert_no_execution(db_session, project.id)

    def test_batch_mock_case_rejected(self, db_session, project):
        c1 = _make_case(db_session, project)
        code1 = _make_code(db_session, c1.id, steps=json.loads(c1.steps))
        self._seed_batch(db_session, project, "batch-mock",
                         {c1.id: ("success", code1.id, True)})
        result = _admit(db_session, project.id, [c1.id], batch_id="batch-mock")
        assert result.ok is False
        assert _assert_no_execution(db_session, project.id)