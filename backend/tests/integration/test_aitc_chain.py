"""端到端：Evidence Snapshot → Draft → 人工评审 → Promote → 挂载既有链（全 mock AI）

EXT-AITC-10A 节拍 1（RED 先行）。
"""

import json

from app.models.ai_case_draft import AiCaseDraft
from app.models.evidence_snapshot import EvidenceSnapshot
from app.models.generated_code import GeneratedCode
from app.models.project import Project
from app.services.ai_case_service import AICaseService

_PAYLOAD = {
    "schema_version": 1,
    "case_name": "端到端用例",
    "priority": "P2",
    "preconditions": [],
    "steps": [{"step_number": 1, "action": "navigate",
               "target": "https://example.com",
               "evidence_ref": "#app", "evidence_snapshot_id": 1},
              {"step_number": 2, "action": "assert_visible",
               "target": "#app", "evidence_ref": "#app",
               "evidence_snapshot_id": 1}],
    "expected_result": "页面可见",
    "ai_assessment": "recommended",
}


def test_chain_snapshot_draft_review_promote(db_session):
    """最小全链闭环：snapshot → draft → approved → Promote → 既有链可消费"""
    project = Project(name="E2E", target_url="https://example.com", test_path="/",
                      browser_type="chromium", headless=True)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    snap = EvidenceSnapshot(project_id=project.id, source_url="https://example.com",
                            snapshot_hash="e2e-hash", element_count=2,
                            crawl_timestamp="2026-10-07 12:00:00")
    db_session.add(snap)
    db_session.commit()
    db_session.refresh(snap)

    svc = AICaseService(db_session)
    draft = svc.create_draft(project.id, snap.id, _PAYLOAD, draft_key="e2e-1")
    assert draft.validation_status in ("valid", "invalid")

    svc.set_review(draft.id, "approved", "ok")
    case = svc.promote(draft.id)

    assert case.source == "ai_draft"
    steps = json.loads(case.steps)
    assert len(steps) == 2, "Promote 必须保留完整步骤结构"

    # 既有链可消费：GeneratedCode 可挂载在该 TestCase 上（不新建执行域事实）
    gc = GeneratedCode(case_id=case.id, code_content="async def run_test(page):\n    return {}",
                       code_language="python", is_valid=True, ai_model="mock")
    db_session.add(gc)
    db_session.commit()
    assert gc.id is not None

    row = db_session.query(AiCaseDraft).filter(AiCaseDraft.id == draft.id).one()
    assert row.promoted_case_id == case.id
