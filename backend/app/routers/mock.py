"""Mock 服务路由 — servers / rules CRUD + dry-run 匹配测试

PROJ-V20-MOCK（F3）。Spec §6 / §8：
  - Mock 是逻辑命名空间（无端口无进程），仅 Web 执行链注入；
  - dry-run 只做匹配校验（无副作用：不执行、不写库）。
"""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.exceptions import NotFoundException
from app.models.mock import (
    MockRule,
    MockRuleCreate,
    MockRuleResponse,
    MockRuleUpdate,
    MockServer,
    MockServerCreate,
    MockServerResponse,
    MockServerUpdate,
)
from app.models.project import Project
from app.schemas import ApiResponse
from app.services.mock_service import MockService, normalize_base_path

router = APIRouter(tags=["Mock 服务"])


def _server_out(server: MockServer) -> dict:
    return MockServerResponse.model_validate(server).model_dump()


def _rule_out(rule: MockRule) -> dict:
    return MockRuleResponse.model_validate(rule).model_dump()


def _ensure_project(db: Session, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise NotFoundException(f"项目 {project_id} 不存在")
    return project


def _get_server(db: Session, server_id: int) -> MockServer:
    server = db.query(MockServer).filter(MockServer.id == server_id).first()
    if server is None:
        raise NotFoundException(f"Mock server {server_id} 不存在")
    return server


def _get_rule(db: Session, rule_id: int) -> MockRule:
    rule = db.query(MockRule).filter(MockRule.id == rule_id).first()
    if rule is None:
        raise NotFoundException(f"Mock 规则 {rule_id} 不存在")
    return rule


# ═══════════════════════════════════════════════
# servers CRUD
# ═══════════════════════════════════════════════

@router.post(
    "/projects/{project_id}/mock-servers",
    response_model=ApiResponse,
    summary="创建 Mock server",
)
def create_server(project_id: int, body: MockServerCreate, db: Session = Depends(get_db)):
    _ensure_project(db, project_id)
    server = MockServer(
        project_id=project_id,
        name=body.name,
        base_path=normalize_base_path(body.base_path),
        enabled=body.enabled,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return ApiResponse(data=_server_out(server))


@router.get(
    "/projects/{project_id}/mock-servers",
    response_model=ApiResponse,
    summary="项目下的 Mock server 列表",
)
def list_servers(project_id: int, db: Session = Depends(get_db)):
    _ensure_project(db, project_id)
    rows = (
        db.query(MockServer)
        .filter(MockServer.project_id == project_id)
        .order_by(MockServer.id.desc())
        .all()
    )
    return ApiResponse(data={"items": [_server_out(r) for r in rows], "total": len(rows)})


@router.get("/mock-servers/{server_id}", response_model=ApiResponse, summary="Mock server 详情")
def get_server(server_id: int, db: Session = Depends(get_db)):
    return ApiResponse(data=_server_out(_get_server(db, server_id)))


@router.put("/mock-servers/{server_id}", response_model=ApiResponse, summary="更新 Mock server")
def update_server(server_id: int, body: MockServerUpdate, db: Session = Depends(get_db)):
    server = _get_server(db, server_id)
    if body.name is not None:
        server.name = body.name
    if body.base_path is not None:
        server.base_path = normalize_base_path(body.base_path)
    if body.enabled is not None:
        server.enabled = body.enabled
    db.commit()
    db.refresh(server)
    return ApiResponse(data=_server_out(server))


@router.delete("/mock-servers/{server_id}", response_model=ApiResponse, summary="删除 Mock server")
def delete_server(server_id: int, db: Session = Depends(get_db)):
    server = _get_server(db, server_id)
    # 历史保护：存在规则 → 拒绝删除（FK RESTRICT 的友好前置）
    if db.query(MockRule).filter(MockRule.server_id == server_id).first():
        from app.exceptions import AppException
        raise AppException(code=409, message="Mock server 下存在规则，禁止删除", status_code=409)
    db.delete(server)
    db.commit()
    return ApiResponse(data={"id": server_id, "deleted": True})


# ═══════════════════════════════════════════════
# rules CRUD
# ═══════════════════════════════════════════════

@router.get(
    "/mock-servers/{server_id}/rules",
    response_model=ApiResponse,
    summary="Mock server 的规则列表",
)
def list_rules(server_id: int, db: Session = Depends(get_db)):
    _get_server(db, server_id)
    rows = (
        db.query(MockRule)
        .filter(MockRule.server_id == server_id)
        .order_by(MockRule.id.asc())
        .all()
    )
    return ApiResponse(data={"items": [_rule_out(r) for r in rows], "total": len(rows)})


@router.post(
    "/mock-servers/{server_id}/rules",
    response_model=ApiResponse,
    summary="创建 Mock 规则",
)
def create_rule(server_id: int, body: MockRuleCreate, db: Session = Depends(get_db)):
    _get_server(db, server_id)
    rule = MockRule(
        server_id=server_id,
        method=body.method,
        path_pattern=body.path_pattern,
        status_code=body.status_code,
        response_body=json.dumps(body.response_body, ensure_ascii=False),
        response_headers=(json.dumps(body.response_headers, ensure_ascii=False)
                          if body.response_headers is not None else None),
        delay_ms=body.delay_ms,
        enabled=body.enabled,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return ApiResponse(data=_rule_out(rule))


@router.put("/mock-rules/{rule_id}", response_model=ApiResponse, summary="更新 Mock 规则")
def update_rule(rule_id: int, body: MockRuleUpdate, db: Session = Depends(get_db)):
    rule = _get_rule(db, rule_id)
    if body.method is not None:
        rule.method = body.method
    if body.path_pattern is not None:
        rule.path_pattern = body.path_pattern
    if body.status_code is not None:
        rule.status_code = body.status_code
    if body.response_body is not None:
        rule.response_body = json.dumps(body.response_body, ensure_ascii=False)
    if body.response_headers is not None:
        rule.response_headers = json.dumps(body.response_headers, ensure_ascii=False)
    if body.delay_ms is not None:
        rule.delay_ms = body.delay_ms
    if body.enabled is not None:
        rule.enabled = body.enabled
    db.commit()
    db.refresh(rule)
    return ApiResponse(data=_rule_out(rule))


@router.delete("/mock-rules/{rule_id}", response_model=ApiResponse, summary="删除 Mock 规则")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = _get_rule(db, rule_id)
    db.delete(rule)
    db.commit()
    return ApiResponse(data={"id": rule_id, "deleted": True})


# ═══════════════════════════════════════════════
# dry-run 匹配测试（无副作用）
# ═══════════════════════════════════════════════

class DryRunBody(BaseModel):
    method: str = Field(default="GET", pattern="^(GET|POST|PUT|DELETE)$")
    path: str = Field(..., min_length=1, max_length=512)


@router.post(
    "/mock-servers/{server_id}/test",
    response_model=ApiResponse,
    summary="dry-run 匹配校验（无副作用）",
)
def dry_run(server_id: int, body: DryRunBody, db: Session = Depends(get_db)):
    _get_server(db, server_id)
    return ApiResponse(data=MockService(db).dry_run(server_id, body.method, body.path))
