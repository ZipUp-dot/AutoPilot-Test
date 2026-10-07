"""mock servers / rules 管理 API（CRUD + dry-run 匹配）— RED 先行

Spec §6 / §11 AC-01：
  - servers CRUD（project 下）+ rules CRUD（server 下）；
  - path_pattern 非法 → 422；
  - dry-run 匹配校验（无副作用：不写库、不执行）。
"""

import pytest


def _mk_server(client, project_id, **overrides):
    body = {"name": "ext-deps", "base_path": "/mock", "enabled": True}
    body.update(overrides)
    return client.post(f"/api/v1/projects/{project_id}/mock-servers", json=body)


def _mk_rule(client, server_id, **overrides):
    body = {
        "method": "GET",
        "path_pattern": "/api/users/:id",
        "status_code": 200,
        "response_body": {"ok": True},
        "response_headers": {"X-Mock": "1"},
        "delay_ms": 0,
        "enabled": True,
    }
    body.update(overrides)
    return client.post(f"/api/v1/mock-servers/{server_id}/rules", json=body)


class TestServerCrud:
    def test_create_and_get(self, client, sample_project):
        r = _mk_server(client, sample_project.id)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["project_id"] == sample_project.id
        assert d["base_path"] == "/mock"
        assert d["enabled"] is True
        got = client.get(f"/api/v1/mock-servers/{d['id']}")
        assert got.status_code == 200
        assert got.json()["data"]["name"] == "ext-deps"

    def test_create_unknown_project_404(self, client):
        assert _mk_server(client, 999999).status_code == 404

    def test_list(self, client, sample_project):
        _mk_server(client, sample_project.id, name="a")
        _mk_server(client, sample_project.id, name="b")
        r = client.get(f"/api/v1/projects/{sample_project.id}/mock-servers")
        assert r.status_code == 200
        assert r.json()["data"]["total"] == 2

    def test_update_and_delete(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        u = client.put(f"/api/v1/mock-servers/{sid}", json={"enabled": False, "base_path": "dep"})
        assert u.status_code == 200, u.text
        assert u.json()["data"]["enabled"] is False
        assert u.json()["data"]["base_path"] == "/dep"
        assert client.delete(f"/api/v1/mock-servers/{sid}").status_code == 200
        assert client.get(f"/api/v1/mock-servers/{sid}").status_code == 404


class TestRuleCrud:
    def test_create_returns_fields(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        r = _mk_rule(client, sid)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["server_id"] == sid
        assert d["method"] == "GET"
        assert d["path_pattern"] == "/api/users/:id"
        assert d["status_code"] == 200
        assert d["response_body"] == {"ok": True}
        assert d["response_headers"] == {"X-Mock": "1"}

    @pytest.mark.parametrize("bad", ["", "api/users", "/api/ :id", "/api/x?y=1", "/api/:/z"])
    def test_bad_path_pattern_422(self, client, sample_project, bad):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        r = _mk_rule(client, sid, path_pattern=bad)
        assert r.status_code == 422, f"{bad!r} 应 422, got {r.status_code}"

    def test_bad_method_422(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        assert _mk_rule(client, sid, method="TRACE").status_code == 422

    def test_list_update_delete(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        rid = _mk_rule(client, sid).json()["data"]["id"]
        lst = client.get(f"/api/v1/mock-servers/{sid}/rules")
        assert lst.json()["data"]["total"] == 1
        u = client.put(f"/api/v1/mock-rules/{rid}", json={"delay_ms": 120, "enabled": False})
        assert u.status_code == 200, u.text
        assert u.json()["data"]["delay_ms"] == 120
        assert u.json()["data"]["enabled"] is False
        assert client.delete(f"/api/v1/mock-rules/{rid}").status_code == 200

    def test_unknown_server_404(self, client, sample_project):
        assert _mk_rule(client, 999999).status_code == 404

    def test_update_unknown_rule_404(self, client, sample_project):
        assert client.put("/api/v1/mock-rules/999999", json={"delay_ms": 1}).status_code == 404


class TestDryRun:
    def test_dry_run_matched(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        rid = _mk_rule(client, sid).json()["data"]["id"]
        r = client.post(f"/api/v1/mock-servers/{sid}/test",
                        json={"method": "GET", "path": "/api/users/42"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["matched"] is True
        assert d["rule_id"] == rid
        assert d["status_code"] == 200

    def test_dry_run_unmatched(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        _mk_rule(client, sid)
        r = client.post(f"/api/v1/mock-servers/{sid}/test",
                        json={"method": "GET", "path": "/other"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["matched"] is False

    def test_dry_run_has_no_side_effect(self, client, sample_project):
        sid = _mk_server(client, sample_project.id).json()["data"]["id"]
        _mk_rule(client, sid)
        before = client.get(f"/api/v1/mock-servers/{sid}/rules").json()["data"]["total"]
        client.post(f"/api/v1/mock-servers/{sid}/test",
                    json={"method": "GET", "path": "/api/users/1"})
        after = client.get(f"/api/v1/mock-servers/{sid}/rules").json()["data"]["total"]
        assert before == after
