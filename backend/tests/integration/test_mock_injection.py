"""AC-02 / AC-03：mock 注入链（Manifest 冻结 → context.route → handler）— RED 先行

Spec §5 / §6 / §8 / §11：
  - AC-02：执行配置的 mock_server_id 由 Admission 冻结进 Manifest，
    执行期 playwright_service 依 Manifest 注册 Playwright route 拦截，命中返回定制响应；
  - AC-03：未匹配路径放行真实请求（continue_），不阻断。

本测试以「Manifest 快照 + Playwright context 替身」验证注入链，
不启动真实浏览器（Spec §12：Playwright context mock）。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.execution import Execution
from app.models.mock import MockRule, MockServer
from app.services.execution_admission_service import ExecutionAdmissionService
from app.services.playwright_service import PlaywrightService, _env_from_manifest


def _seed_mock(db, project_id):
    server = MockServer(project_id=project_id, name="dep", base_path="/mock", enabled=True)
    db.add(server)
    db.commit()
    db.refresh(server)
    db.add(MockRule(server_id=server.id, method="GET", path_pattern="/api/users/:id",
                    status_code=201, response_body='{"mock":true}',
                    response_headers='{"X-Mock":"1"}', delay_ms=0, enabled=True))
    db.commit()
    return server


class TestManifestCarriesMockServerId:
    """AC-02（前半）：mock_server_id 经 Admission 冻结进 Manifest"""

    def test_admission_freezes_mock_server_id(self, db_session, sample_project,
                                             sample_test_case, sample_generated_code):
        sample_project.config_json = json.dumps({"mock_server_id": 77})
        db_session.commit()

        svc = ExecutionAdmissionService(db_session)
        result = svc.admit(sample_project.id, [sample_test_case.id],
                           execution_mode="headless")
        assert result.ok, result.errors
        assert result.mock_server_id == 77

        eid = svc.materialize(result)
        row = db_session.query(Execution).filter(Execution.id == eid).first()
        manifest = json.loads(row.manifest_json)
        assert manifest["mock_server_id"] == 77

    def test_admission_without_mock_server_id(self, db_session, sample_project,
                                              sample_test_case, sample_generated_code):
        svc = ExecutionAdmissionService(db_session)
        result = svc.admit(sample_project.id, [sample_test_case.id],
                           execution_mode="headless")
        assert result.ok, result.errors
        assert result.mock_server_id is None

    def test_env_from_manifest_reads_mock_server_id(self):
        assert _env_from_manifest({"mock_server_id": 5})["mock_server_id"] == 5
        assert _env_from_manifest({})["mock_server_id"] is None


class TestInjectionAtExecution:
    """AC-02（后半）/ AC-03：执行期按 Manifest 注册拦截 + 未匹配放行"""

    async def test_install_registers_and_fulfills(self, db_session, sample_project):
        server = _seed_mock(db_session, sample_project.id)
        ctx = SimpleNamespace(route=AsyncMock())
        svc = PlaywrightService(db_session)

        await svc._install_mock_if_configured(ctx, {"mock_server_id": server.id})

        ctx.route.assert_awaited_once()
        glob, handler = ctx.route.call_args.args
        assert glob == "**/mock/**"

        route = SimpleNamespace(fulfill=AsyncMock(), continue_=AsyncMock())
        outcome = await handler(route, SimpleNamespace(
            method="GET", url="https://dep.example.com/mock/api/users/7"))

        assert outcome == "fulfilled"
        route.continue_.assert_not_awaited()
        kw = route.fulfill.call_args.kwargs
        assert kw["status"] == 201
        assert json.loads(kw["body"]) == {"mock": True}
        assert kw["headers"]["X-Mock"] == "1"

    async def test_unmatched_path_passes_through(self, db_session, sample_project):
        server = _seed_mock(db_session, sample_project.id)
        ctx = SimpleNamespace(route=AsyncMock())
        svc = PlaywrightService(db_session)
        await svc._install_mock_if_configured(ctx, {"mock_server_id": server.id})
        _, handler = ctx.route.call_args.args

        route = SimpleNamespace(fulfill=AsyncMock(), fallback=AsyncMock(),
                                continue_=AsyncMock())
        outcome = await handler(route, SimpleNamespace(
            method="GET", url="https://dep.example.com/mock/unmapped/x"))

        assert outcome == "passed"
        route.fulfill.assert_not_awaited()
        # fallback(): 交回既有 SSRF 策略链放行真实请求（continue_ 会绕过 SSRF）
        route.fallback.assert_awaited_once()
        route.continue_.assert_not_awaited()

    async def test_no_mock_server_id_no_registration(self, db_session):
        ctx = SimpleNamespace(route=AsyncMock())
        svc = PlaywrightService(db_session)
        await svc._install_mock_if_configured(ctx, {})
        ctx.route.assert_not_awaited()

    async def test_disabled_server_no_registration(self, db_session, sample_project):
        server = _seed_mock(db_session, sample_project.id)
        server.enabled = False
        db_session.commit()
        ctx = SimpleNamespace(route=AsyncMock())
        svc = PlaywrightService(db_session)
        await svc._install_mock_if_configured(ctx, {"mock_server_id": server.id})
        ctx.route.assert_not_awaited()
