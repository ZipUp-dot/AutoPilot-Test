"""AC-04/07：Promote 语义 —— 创建 TestCase(source=ai_draft)，且零执行副作用

EXT-AITC-10A 节拍 1（RED 先行）。
"""

import json

import pytest

from app.models.ai_case_draft import AiCaseDraft
from app.models.evidence_snapshot import EvidenceSnapshot
from app.models.execution import Execution
from app.models.execution_step import ExecutionStep
from app.models.generated_code import GeneratedCode
from app.models.project import Project
from app.models.test_case import TestCase
from app.services.ai_case_service import AICaseService


def _project(db):
    p = Project(name="PROMOTE", target_url="https://example.com", test_path="/",
                browser_type="chromium", headless=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _snapshot(db, project, h="hash-v1"):
    s = EvidenceSnapshot(project_id=project.id, source_url="https://example.com",
                         snapshot_hash=h, element_count=1,
                         crawl_timestamp="2026-10-07 12:00:00")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _payload():
    return {
        "schema_version": 1,
        "case_name": "Promote 用例",
        "priority": "P1",
        "preconditions": [],
        "steps": [{"step_number": 1, "action": "navigate",
                   "target": "https://example.com",
                   "evidence_ref": "#main", "evidence_snapshot_id": 1}],
        "expected_result": "加载成功",
        "ai_assessment": "recommended",
    }


def _approved_draft(db, project, snap):
    svc = AICaseService(db)
    d = svc.create_draft(project.id, snap.id, _payload(), draft_key="pk1")
    svc.set_review(d.id, "approved", "ok")
    return d


def test_promote_creates_test_case_with_source_ai_draft(db_session):
    """AC-04：valid ∧ approved ∧ snapshot 复验一致 → 创建 TestCase(source='ai_draft')"""
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    d = _approved_draft(db_session, project, snap)

    case = AICaseService(db_session).promote(d.id)

    assert isinstance(case, TestCase)
    assert case.source == "ai_draft", "Promote 产物 provenance 必须为 ai_draft"
    assert json.loads(case.steps), "steps 必须为合法 JSON"

    row = db_session.query(AiCaseDraft).filter(AiCaseDraft.id == d.id).one()
    assert row.promoted_case_id == case.id, "终态须回填 promoted_case_id"


def test_promote_produces_zero_execution_side_effects(db_session):
    """AC-07：Promote 不得创建 Execution / ExecutionStep / GeneratedCode"""
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    d = _approved_draft(db_session, project, snap)

    AICaseService(db_session).promote(d.id)

    assert db_session.query(Execution).count() == 0, "Promote 不得创建 Execution"
    assert db_session.query(ExecutionStep).count() == 0, "Promote 不得创建 ExecutionStep"
    assert db_session.query(GeneratedCode).count() == 0, "Promote 不得创建 GeneratedCode"


def test_promote_requires_approved_review(db_session):
    """Promote 前置：未 approved 一律拒绝"""
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    svc = AICaseService(db_session)
    d = svc.create_draft(project.id, snap.id, _payload(), draft_key="pk2")

    with pytest.raises(Exception):
        svc.promote(d.id)
    assert db_session.query(TestCase).count() == 0
