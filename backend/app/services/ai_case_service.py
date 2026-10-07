"""AI Case Service —— AI TestCase 候选链的唯一写入者（EXT-AITC-10A）

职责（Spec §6）：生成落库 / System Validation / 版本管理 / 人工评审写入 / Promote。

铁律：
  - 三维独立、永不合并（8.5）：ai_assessment ≠ validation_status ≠ review_status；
  - Promote 前置 = valid ∧ approved ∧ snapshot 复验一致（8.4），
    复验不一致 → review_status=needs_review，**禁止自动拒绝**（8.2）；
  - Promote 只创建 TestCase（走既有 CaseService），**不得**创建
    Execution / ExecutionStep / GeneratedCode（8.8）；
  - 版本语义：needs_edit → 新版本行，旧行终态冻结且旧 approval 失效（8.6）；
  - source 仅 provenance，不参与 StepCanonicalizer / source_steps_hash（8.8）。
"""

import hashlib
import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ai_case_draft import AiCaseDraft
from app.models.evidence_snapshot import EvidenceSnapshot
from app.models.test_case import TestCase
from app.services.case_service import CaseService
from app.utils.excel_parser import VALID_ACTIONS

# 三维取值域（钉死，禁止扩张）
AI_ASSESSMENTS = frozenset({"recommended", "needs_review", "not_ready"})
VALIDATION_STATUSES = frozenset({"valid", "invalid"})
REVIEW_STATUSES = frozenset({"pending", "needs_edit", "approved", "rejected"})

REQUIRED_FIELDS = (
    "schema_version", "case_name", "priority", "preconditions",
    "steps", "expected_result", "ai_assessment",
)


class AICaseException(Exception):
    """AI TestCase 链领域异常"""


class AICaseService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._case_service = CaseService(db)

    # ═══════════════════════════════════════════════
    # System Validation（三维之二）
    # ═══════════════════════════════════════════════

    def validate_payload(self, payload: dict) -> list[dict]:
        """JSON Schema + action 白名单校验；返回错误明细（[] = valid）"""
        errors: list[dict] = []
        payload = payload or {}

        for field in REQUIRED_FIELDS:
            # 空列表视为"已提供"（如 preconditions=[] 合法）；steps 的空值
            # 由下方专用分支判定为 empty_or_not_list，不在此重复计错。
            if payload.get(field) in (None, ""):
                errors.append({"field": field, "error": "missing_required"})

        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append({"field": "steps", "error": "empty_or_not_list"})
        else:
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append({"field": f"steps[{idx}]", "error": "not_object"})
                    continue
                action = step.get("action")
                if action not in VALID_ACTIONS:
                    errors.append({
                        "field": f"steps[{idx}].action",
                        "error": "action_not_allowed",
                        "value": action,
                    })
                if not step.get("target"):
                    errors.append({"field": f"steps[{idx}].target",
                                   "error": "missing_target"})

        assessment = payload.get("ai_assessment")
        if assessment is not None and assessment not in AI_ASSESSMENTS:
            errors.append({"field": "ai_assessment", "error": "invalid_value",
                           "value": assessment})
        return errors

    # ═══════════════════════════════════════════════
    # Draft 落库 / 版本
    # ═══════════════════════════════════════════════

    def create_draft(self, project_id: int, snapshot_id: int, payload: dict,
                     draft_key: Optional[str] = None) -> AiCaseDraft:
        errors = self.validate_payload(payload)
        key = draft_key or hashlib.sha256(
            f"{project_id}:{(payload or {}).get('case_name', '')}".encode("utf-8")
        ).hexdigest()[:32]

        draft = AiCaseDraft(
            project_id=project_id,
            snapshot_id=snapshot_id,
            draft_key=key,
            draft_version=1,
            case_name=(payload or {}).get("case_name") or "未命名用例",
            priority=(payload or {}).get("priority"),
            preconditions=json.dumps((payload or {}).get("preconditions") or [],
                                     ensure_ascii=False),
            steps=json.dumps((payload or {}).get("steps") or [], ensure_ascii=False),
            expected_result=(payload or {}).get("expected_result"),
            ai_assessment=(payload or {}).get("ai_assessment"),
            validation_status="invalid" if errors else "valid",
            validation_errors=json.dumps(errors, ensure_ascii=False) if errors else None,
            review_status="pending",
        )
        self._db.add(draft)
        self._db.commit()
        self._db.refresh(draft)
        return draft

    def edit_draft(self, draft_id: int, payload: dict) -> AiCaseDraft:
        """needs_edit → 新版本行；旧行终态冻结、旧 approval 失效（8.6）"""
        old = self._get(draft_id)
        latest = (
            self._db.query(AiCaseDraft)
            .filter(AiCaseDraft.project_id == old.project_id,
                    AiCaseDraft.draft_key == old.draft_key)
            .order_by(AiCaseDraft.draft_version.desc())
            .first()
        )
        next_version = (latest.draft_version if latest else old.draft_version) + 1

        errors = self.validate_payload(payload)
        new = AiCaseDraft(
            project_id=old.project_id,
            snapshot_id=old.snapshot_id,
            draft_key=old.draft_key,
            draft_version=next_version,
            case_name=(payload or {}).get("case_name") or old.case_name,
            priority=(payload or {}).get("priority", old.priority),
            preconditions=json.dumps((payload or {}).get("preconditions") or [],
                                     ensure_ascii=False),
            steps=json.dumps((payload or {}).get("steps") or [], ensure_ascii=False),
            expected_result=(payload or {}).get("expected_result"),
            ai_assessment=(payload or {}).get("ai_assessment"),
            validation_status="invalid" if errors else "valid",
            validation_errors=json.dumps(errors, ensure_ascii=False) if errors else None,
            review_status="pending",
        )
        # 旧版本终态冻结（不删除）；旧 approval 立即失效
        old.review_status = "needs_edit"
        self._db.add(new)
        self._db.commit()
        self._db.refresh(new)
        return new

    # ═══════════════════════════════════════════════
    # Human Review 写入（三维之三，由 10B 调用）
    # ═══════════════════════════════════════════════

    def set_review(self, draft_id: int, review_status: str,
                   comment: Optional[str] = None) -> AiCaseDraft:
        if review_status not in REVIEW_STATUSES:
            raise AICaseException(f"非法 review_status: {review_status}")
        draft = self._get(draft_id)
        draft.review_status = review_status
        draft.review_comment = comment
        self._db.commit()
        self._db.refresh(draft)
        return draft

    # ═══════════════════════════════════════════════
    # Promote
    # ═══════════════════════════════════════════════

    def promote(self, draft_id: int) -> TestCase:
        """valid ∧ approved ∧ snapshot 复验一致 → 创建 TestCase(source=ai_draft)"""
        draft = self._get(draft_id)
        if draft.promoted_case_id is not None:
            raise AICaseException("该 Draft 已 Promote，禁止重复创建")

        if draft.validation_status != "valid":
            raise AICaseException(
                f"System Validation 未通过（{draft.validation_status}），禁止 Promote"
            )
        if draft.review_status != "approved":
            raise AICaseException(
                f"Human Review 未批准（{draft.review_status}），禁止 Promote"
            )

        # 复验：Draft 绑定的 snapshot 必须仍是当前有效 Evidence Snapshot（8.4）
        bound = self._db.query(EvidenceSnapshot).filter(
            EvidenceSnapshot.id == draft.snapshot_id).first()
        current = (
            self._db.query(EvidenceSnapshot)
            .filter(EvidenceSnapshot.project_id == draft.project_id)
            .order_by(EvidenceSnapshot.crawl_timestamp.desc(),
                      EvidenceSnapshot.id.desc())
            .first()
        )
        if bound is not None and current is not None and current.snapshot_hash != bound.snapshot_hash:
            # 页面正常演进：置 needs_review 交人工，禁止自动拒绝（8.2）
            draft.review_status = "needs_review"
            self._db.commit()
            raise AICaseException("Evidence Snapshot 已演进，需人工复核后方可 Promote")

        case = self._case_service.create(
            project_id=draft.project_id,
            case_name=draft.case_name,
            steps=json.loads(draft.steps or "[]"),
            source="ai_draft",
            priority=draft.priority or "P1",
            pre_conditions=draft.preconditions,
            expected_result=draft.expected_result,
        )
        draft.promoted_case_id = case.id
        self._db.commit()
        self._db.refresh(draft)
        return case

    # ═══════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════

    def list_drafts(self, project_id: Optional[int] = None,
                    review_status: Optional[str] = None) -> list[AiCaseDraft]:
        q = self._db.query(AiCaseDraft)
        if project_id is not None:
            q = q.filter(AiCaseDraft.project_id == project_id)
        if review_status is not None:
            q = q.filter(AiCaseDraft.review_status == review_status)
        return q.order_by(AiCaseDraft.id.desc()).all()

    def get_draft(self, draft_id: int) -> AiCaseDraft:
        return self._get(draft_id)

    def _get(self, draft_id: int) -> AiCaseDraft:
        draft = self._db.query(AiCaseDraft).filter(AiCaseDraft.id == draft_id).first()
        if not draft:
            raise AICaseException(f"Draft {draft_id} 不存在")
        return draft
