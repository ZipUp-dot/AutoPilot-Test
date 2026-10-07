"""AC-02/03/05/08/09：AI Case Draft 服务（三维独立 / 版本语义 / 复验）

EXT-AITC-10A 节拍 1（RED 先行）。目标模块 app/services/ai_case_service.py 尚未实现。
"""

import inspect
import json

import pytest

from app.models.ai_case_draft import AiCaseDraft
from app.models.evidence_snapshot import EvidenceSnapshot
from app.models.project import Project
from app.models.test_case import TestCase
from app.services.ai_case_service import AICaseService
from app.utils.step_canonicalizer import canonicalize_steps, hash_steps

VALID_ACTIONS = frozenset({
    "navigate", "fill", "click", "select", "hover",
    "assert_text", "assert_visible", "screenshot", "wait", "swipe", "back",
})

_STEPS = [{"step_number": 1, "action": "navigate", "target": "https://example.com"}]


def _project(db, name="AITC"):
    p = Project(name=name, target_url="https://example.com", test_path="/",
                browser_type="chromium", headless=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _snapshot(db, project, snapshot_hash="hash-v1"):
    s = EvidenceSnapshot(project_id=project.id, source_url="https://example.com",
                         snapshot_hash=snapshot_hash, element_count=3,
                         crawl_timestamp="2026-10-07 12:00:00")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _payload(**over):
    base = {
        "schema_version": 1,
        "case_name": "登录成功",
        "priority": "P1",
        "preconditions": [],
        "steps": [dict(_STEPS[0], evidence_ref="#login", evidence_snapshot_id=1)],
        "expected_result": "进入首页",
        "ai_assessment": "recommended",
    }
    base.update(over)
    return base


def _mk_draft(db, project, snapshot, payload=None, draft_key="k1"):
    svc = AICaseService(db)
    return svc.create_draft(project.id, snapshot.id, payload or _payload(), draft_key=draft_key)


# ── AC-02 · JSON Schema 校验 ──

def test_missing_required_field_is_invalid(db_session):
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    bad = _payload()
    bad.pop("case_name")
    errs = AICaseService(db_session).validate_payload(bad)
    assert errs, "缺必填字段必须返回错误明细"


def test_self_invented_action_is_invalid(db_session):
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    bad = _payload(steps=[{"step_number": 1, "action": "do_magic", "target": "#x"}])
    errs = AICaseService(db_session).validate_payload(bad)
    assert errs, "自造 action 必须 invalid"


def test_whitelist_actions_all_accepted(db_session):
    project = _project(db_session)
    _snapshot(db_session, project)
    steps = [{"step_number": i + 1, "action": a, "target": "#x"}
             for i, a in enumerate(sorted(VALID_ACTIONS))]
    assert AICaseService(db_session).validate_payload(_payload(steps=steps)) == []


# ── AC-03 · 三维独立存储，永不合并 ──

def test_three_dimensions_are_independent_columns(db_session):
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    svc = AICaseService(db_session)
    d = _mk_draft(db_session, project, snap)
    svc.set_review(d.id, "pending", None)
    row = db_session.query(AiCaseDraft).filter(AiCaseDraft.id == d.id).one()
    assert row.ai_assessment == "recommended"
    assert row.validation_status in ("valid", "invalid")
    assert row.review_status == "pending"
    # 三者必须可独立取值（不得由任一派生）
    assert {row.ai_assessment, row.validation_status, row.review_status} != set()


# ── AC-05 · snapshot 复验不一致 → needs_review（不自动拒绝、不 Promote）──

def test_snapshot_drift_sets_needs_review_not_rejected(db_session):
    project = _project(db_session)
    snap = _snapshot(db_session, project, "hash-v1")
    svc = AICaseService(db_session)
    d = _mk_draft(db_session, project, snap)
    svc.set_review(d.id, "approved", "ok")
    # 页面演进：新抓取产生新的有效 snapshot
    _snapshot(db_session, project, "hash-v2")
    with pytest.raises(Exception):
        svc.promote(d.id)
    row = db_session.query(AiCaseDraft).filter(AiCaseDraft.id == d.id).one()
    assert row.review_status == "needs_review", "不一致必须落 needs_review"
    assert row.review_status != "rejected", "禁止自动拒绝"
    assert row.promoted_case_id is None, "不一致不得 Promote"


# ── AC-08 · needs_edit → 新版本，旧 approval 失效 ──

def test_needs_edit_creates_new_version_and_invalidates_old_approval(db_session):
    project = _project(db_session)
    snap = _snapshot(db_session, project)
    svc = AICaseService(db_session)
    v1 = _mk_draft(db_session, project, snap)
    svc.set_review(v1.id, "approved", "ok")
    v2 = svc.edit_draft(v1.id, _payload(case_name="登录成功（改）"))
    assert v2.draft_version == v1.draft_version + 1
    assert v2.draft_key == v1.draft_key
    rows = db_session.query(AiCaseDraft).filter(AiCaseDraft.draft_key == v1.draft_key).all()
    assert len(rows) == 2, "旧版本行必须保留（不 hard delete）"
    old = db_session.query(AiCaseDraft).filter(AiCaseDraft.id == v1.id).one()
    assert old.review_status != "approved", "旧 approval 必须失效"
    with pytest.raises(Exception):
        svc.promote(v1.id)


# ── AC-09 · source 不进入 StepCanonicalizer / source_steps_hash ──

def test_canonicalizer_signature_takes_steps_only():
    params = list(inspect.signature(canonicalize_steps).parameters)
    assert params and all("source" not in p for p in params), params


def test_same_steps_different_source_give_same_hash(db_session):
    project = _project(db_session)
    steps_txt = json.dumps(_STEPS, ensure_ascii=False)
    a = TestCase(project_id=project.id, case_name="A", steps=steps_txt, source="excel")
    b = TestCase(project_id=project.id, case_name="B", steps=steps_txt, source="ai_draft")
    db_session.add_all([a, b])
    db_session.commit()
    assert hash_steps(json.loads(a.steps)) == hash_steps(json.loads(b.steps))
