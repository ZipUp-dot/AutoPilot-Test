"""CodeValidator 统一校验 + get_effective_code 测试

背景：生成（ai_service）与执行（code_validator）两套规则统一到 CodeValidator，
GeneratedCode 记录 source_steps_hash 做来源绑定，get_effective_code 做 validate-on-load。
"""

import json
from datetime import datetime

import pytest

from app.services.ai_service import AIService
from app.utils.code_validator import get_effective_code, CodeValidator
from app.utils.step_canonicalizer import hash_steps
from app.models.generated_code import GeneratedCode


STEPS = [{"step_number": 1, "action": "click", "target": "#btn",
          "value": "", "description": "go"}]
VALID_WEB = ("async def run_test(page):\n"
             "    return {'success': True, 'steps': []}")
# 违反当前 Validator：直接访问原生 page 对象
INVALID_WEB = ("async def run_test(page):\n"
               "    await page.goto('https://example.com')\n"
               "    return {'success': True, 'steps': []}")


def _make_case(db_session, sample_project, steps=None):
    from app.models.test_case import TestCase
    case = TestCase(
        project_id=sample_project.id,
        case_name="C",
        steps=json.dumps(steps or STEPS),
        status="imported",
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


class TestGenerateSingleUnifiedValidation:
    """generate_single 统一走 CodeValidator"""

    def test_getattr_code_now_invalid(self, db_session, sample_project, sample_test_case, mocker):
        """验收2: 含 getattr 的旧规则可通过代码 → 统一后 is_valid=0"""
        mocker.patch(
            "app.services.ai_service._call_openai",
            return_value=("async def run_test(page):\n"
                          "    getattr(page, 'goto')\n"
                          "    return {'success': True, 'steps': []}"),
        )
        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)
        assert result.is_valid is False
        assert result.syntax_error is not None

    def test_source_steps_hash_persisted(self, db_session, sample_project, sample_test_case):
        """验收3(前半): source_steps_hash 在 generate_single 时落库正确"""
        svc = AIService(db_session)
        result = svc.generate_single(sample_project.id, sample_test_case.id)
        row = db_session.query(GeneratedCode).filter(GeneratedCode.id == result.code_id).first()
        expected = hash_steps(json.loads(sample_test_case.steps))
        assert row.source_steps_hash == expected


class TestGetEffectiveCodeNullSource:
    """验收3(后半): 存量 NULL 行 → 返回 None"""

    def test_null_source_steps_hash_returns_none(self, db_session, sample_project):
        case = _make_case(db_session, sample_project)
        db_session.add(GeneratedCode(
            case_id=case.id, code_content=VALID_WEB, is_valid=1,
        ))  # source_steps_hash 留 NULL
        db_session.commit()

        result = get_effective_code(db_session, case.id, hash_steps(STEPS))
        assert result is None


class TestGetEffectiveCodeHashMismatch:
    """验收4: expected_steps_hash 不匹配 → None，不回退旧版本"""

    def test_no_fallback_to_older_matching_version(self, db_session, sample_project):
        case = _make_case(db_session, sample_project)
        expected = hash_steps(STEPS)
        older_hash = hash_steps([{"step_number": 1, "action": "click", "target": "#old"}])

        # 旧版本：来源与 expected 匹配、且 is_valid=1（本应可用）
        older = GeneratedCode(
            case_id=case.id, code_content=VALID_WEB, is_valid=1,
            source_steps_hash=expected,
            created_at=datetime(2020, 1, 1),
        )
        db_session.add(older)
        # 最新版本：来源哈希与当前 steps 不匹配（TestCase 已改）
        newer = GeneratedCode(
            case_id=case.id, code_content=VALID_WEB, is_valid=1,
            source_steps_hash=older_hash,
            created_at=datetime(2021, 1, 1),
        )
        db_session.add(newer)
        db_session.commit()

        # latest 的 source_steps_hash 不匹配 → 返回 None，禁止回退到旧版本
        assert get_effective_code(db_session, case.id, expected) is None


class TestGetEffectiveCodeValidateOnLoad:
    """验收5: is_valid=1 但违反当前 Validator → 回写 is_valid=0"""

    def test_invalid_stored_row_rewritten(self, db_session, sample_project):
        case = _make_case(db_session, sample_project)
        row = GeneratedCode(
            case_id=case.id, code_content=INVALID_WEB, is_valid=1,
            source_steps_hash=hash_steps(STEPS),
        )
        db_session.add(row)
        db_session.commit()
        row_id = row.id

        result = get_effective_code(db_session, case.id, hash_steps(STEPS))
        assert result is None

        db_session.expire_all()
        reloaded = db_session.query(GeneratedCode).filter(GeneratedCode.id == row_id).first()
        assert reloaded.is_valid == 0
        assert reloaded.syntax_error is not None

    def test_valid_matching_row_returned(self, db_session, sample_project):
        """全部条件满足 → 返回 latest"""
        case = _make_case(db_session, sample_project)
        row = GeneratedCode(
            case_id=case.id, code_content=VALID_WEB, is_valid=1,
            source_steps_hash=hash_steps(STEPS),
        )
        db_session.add(row)
        db_session.commit()

        result = get_effective_code(db_session, case.id, hash_steps(STEPS))
        assert result is not None
        assert result.id == row.id