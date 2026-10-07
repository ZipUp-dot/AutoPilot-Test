"""AI TestCase 候选路由（EXT-AITC-10A §6）— 生成 / 列表 / 详情 / 评审 / 版本 / Promote

契约（供 10B 前端对齐）：
  POST /api/v1/ai-drafts/generate            起生成
  GET  /api/v1/ai-drafts                     列表（?project_id=）
  GET  /api/v1/ai-drafts/{draft_id}          详情
  PUT  /api/v1/ai-drafts/{draft_id}/review   人工三维写入
  POST /api/v1/ai-drafts/{draft_id}/versions 编辑产生新版本
  POST /api/v1/ai-drafts/{draft_id}/promote  Promote

薄路由：全部写入委托 AICaseService（Draft 链唯一写入者）。
数据流（§5）：Evidence Snapshot（8.1）→ Sanitizer（8.3，先于 Prompt）
→ AI 生成 → 校验 → Draft 持久化。
"""

import json
import os
from typing import Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.element import PageElement
from app.models.project import Project
from app.models.test_case import TestCase
from app.schemas import ApiResponse
from app.services import element_extractor
from app.services.ai_case_service import AICaseException, AICaseService
from app.services.ai_service import AIService
from app.utils.evidence_sanitizer import sanitize_elements
from app.utils.url_builder import build_target_url

router = APIRouter(prefix="/ai-drafts", tags=["AI 用例候选"])

# D-1（Owner 已裁）：按元素分区多次生成，防单次输出超长
_PARTITION_SIZE = 40

_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "generate_case_prompt.txt"
)


class GenerateBody(BaseModel):
    project_id: int
    source_url: Optional[str] = None


class ReviewBody(BaseModel):
    review_status: str
    comment: Optional[str] = None


# ═══════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════

def _el_to_dict(el: PageElement) -> dict:
    return {
        "element_type": el.element_type,
        "tag_name": el.tag_name,
        "element_id": el.element_id,
        "name": el.name,
        "class_name": el.class_name,
        "selector": el.selector,
        "text_content": el.text_content,
        "placeholder": el.placeholder,
        "is_visible": el.is_visible,
    }


def _draft_dict(d) -> dict:
    return {
        "id": d.id,
        "project_id": d.project_id,
        "snapshot_id": d.snapshot_id,
        "draft_key": d.draft_key,
        "draft_version": d.draft_version,
        "case_name": d.case_name,
        "priority": d.priority,
        "preconditions": d.preconditions,
        "steps": d.steps,
        "expected_result": d.expected_result,
        "ai_assessment": d.ai_assessment,
        "validation_status": d.validation_status,
        "validation_errors": d.validation_errors,
        "review_status": d.review_status,
        "review_comment": d.review_comment,
        "promoted_case_id": d.promoted_case_id,
        "created_at": str(d.created_at) if d.created_at else None,
        "updated_at": str(d.updated_at) if d.updated_at else None,
    }


def _build_prompt(source_url: str, elements: list[dict], existing_cases: list[str]) -> str:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    return (
        template
        .replace("{{target_url}}", source_url or "")
        .replace("{{elements}}", json.dumps(elements, ensure_ascii=False, indent=2))
        .replace("{{existing_cases}}", json.dumps(existing_cases, ensure_ascii=False))
    )


def _parse_cases(raw: str) -> list[dict]:
    """解析 AI 输出为候选用例列表（容错 markdown 代码块 / 裸数组）"""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) >= 3 else text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return [c for c in data["cases"] if isinstance(c, dict)]
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    return []


# ═══════════════════════════════════════════════
# 起生成
# ═══════════════════════════════════════════════

@router.post("/generate", response_model=ApiResponse, summary="起 AI 用例生成（Evidence → Draft）")
def generate_drafts(body: GenerateBody, db: Session = Depends(get_db)):
    """当前证据 → Snapshot 固化 → 脱敏 → AI 生成 → 校验 → Draft 列表"""
    project = db.query(Project).filter(Project.id == body.project_id).first()
    if not project:
        return ApiResponse(code=404, message=f"项目 {body.project_id} 不存在", data=None)

    rows = (
        db.query(PageElement)
        .filter(PageElement.project_id == project.id,
                PageElement.platform == getattr(project, "platform", "web"),
                PageElement.is_visible == 1)
        .order_by(PageElement.id)
        .all()
    )
    if not rows:
        return ApiResponse(data={"items": [], "total": 0})

    source_url = body.source_url or build_target_url(project.target_url, project.test_path)
    elements = [_el_to_dict(r) for r in rows]

    # 8.1 先固化 Evidence Snapshot（元素落 snapshot_id）→ 8.3 脱敏先于 Prompt
    snap = element_extractor.persist_evidence_snapshot(
        db, project.id, source_url, elements, element_rows=rows
    )
    sanitized = sanitize_elements(elements)

    existing = [
        c.case_name
        for c in db.query(TestCase).filter(TestCase.project_id == project.id).all()
    ][:50]

    svc = AICaseService(db)
    ai = AIService(db)
    created: list[dict] = []
    partitions = [sanitized[i:i + _PARTITION_SIZE]
                  for i in range(0, len(sanitized), _PARTITION_SIZE)] or [[]]

    for p_idx, part in enumerate(partitions):
        prompt = _build_prompt(source_url, part, existing)
        for payload in _parse_cases(ai.generate_test_cases(prompt)):
            payload.setdefault("schema_version", 1)
            for step in payload.get("steps") or []:
                if isinstance(step, dict):
                    step.setdefault("evidence_snapshot_id", snap.id)
            draft = svc.create_draft(
                project.id, snap.id, payload,
                draft_key=f"{project.id}-p{p_idx}-{payload.get('case_name', '')}",
            )
            created.append(_draft_dict(draft))

    return ApiResponse(data={"items": created, "total": len(created)})


# ═══════════════════════════════════════════════
# 列表 / 详情
# ═══════════════════════════════════════════════

@router.get("", response_model=ApiResponse, summary="AI 用例候选列表")
def list_drafts(
    project_id: Optional[int] = None,
    review_status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    items = AICaseService(db).list_drafts(project_id, review_status)
    return ApiResponse(data={"items": [_draft_dict(d) for d in items], "total": len(items)})


@router.get("/{draft_id}", response_model=ApiResponse, summary="AI 用例候选详情")
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    try:
        draft = AICaseService(db).get_draft(draft_id)
    except AICaseException as e:
        return ApiResponse(code=404, message=str(e), data=None)
    return ApiResponse(data=_draft_dict(draft))


# ═══════════════════════════════════════════════
# 人工评审写入 / 版本 / Promote
# ═══════════════════════════════════════════════

@router.put("/{draft_id}/review", response_model=ApiResponse, summary="人工三维写入")
def set_review(draft_id: int, body: ReviewBody, db: Session = Depends(get_db)):
    try:
        draft = AICaseService(db).set_review(draft_id, body.review_status, body.comment)
    except AICaseException as e:
        return ApiResponse(code=422, message=str(e), data=None)
    return ApiResponse(data=_draft_dict(draft))


@router.post("/{draft_id}/versions", response_model=ApiResponse, summary="编辑产生新版本")
def create_version(draft_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """needs_edit → 新 draft_version（旧行终态冻结、旧 approval 失效，8.6）"""
    try:
        draft = AICaseService(db).edit_draft(draft_id, payload)
    except AICaseException as e:
        return ApiResponse(code=404, message=str(e), data=None)
    return ApiResponse(data=_draft_dict(draft))


@router.post("/{draft_id}/promote", response_model=ApiResponse, summary="Promote 为 TestCase")
def promote_draft(draft_id: int, db: Session = Depends(get_db)):
    """valid ∧ approved ∧ snapshot 复验一致 → 创建 TestCase(source=ai_draft)"""
    try:
        case = AICaseService(db).promote(draft_id)
    except AICaseException as e:
        return ApiResponse(code=422, message=str(e), data=None)
    return ApiResponse(data={"case_id": case.id, "source": case.source})
