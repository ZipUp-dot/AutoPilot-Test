"""AC-01：webhook token 校验（常量时间比较）— RED 先行

Spec §6 / §11 AC-01：
  - 非法 / 缺失 token 的 webhook 触发 → 401，且**不产生** pipeline_run；
  - token 未配置时 webhook 触发同样拒绝；
  - 比对必须走常量时间（hmac.compare_digest），防时序侧信道。
"""

import pytest


def _mk_pipeline(client, project_id, *, token="secret-token", enabled=True):
    cfg = {"manual": True}
    if token is not None:
        cfg["webhook_token"] = token
    return client.post(
        f"/api/v1/projects/{project_id}/pipelines",
        json={
            "name": "ci",
            "trigger_config_json": cfg,
            "stages_json": [
                {"name": "smoke", "case_selector": {"case_ids": [1]}, "env": "staging"},
            ],
            "enabled": enabled,
        },
    )


class TestWebhookTokenRejected:
    """AC-01：非法/缺失 token → 401 且零 run"""

    def test_missing_token_401_no_run(self, client, sample_project):
        pid = _mk_pipeline(client, sample_project.id).json()["data"]["id"]
        r = client.post(f"/api/v1/pipelines/{pid}/trigger", json={"trigger_type": "webhook"})
        assert r.status_code == 401, r.text
        runs = client.get(f"/api/v1/pipelines/{pid}/runs").json()["data"]
        assert runs["total"] == 0

    def test_wrong_token_401_no_run(self, client, sample_project):
        pid = _mk_pipeline(client, sample_project.id).json()["data"]["id"]
        r = client.post(
            f"/api/v1/pipelines/{pid}/trigger",
            headers={"X-Pipeline-Token": "wrong-token"},
            json={"trigger_type": "webhook"},
        )
        assert r.status_code == 401, r.text
        runs = client.get(f"/api/v1/pipelines/{pid}/runs").json()["data"]
        assert runs["total"] == 0

    def test_token_not_configured_401(self, client, sample_project):
        pid = _mk_pipeline(client, sample_project.id, token=None).json()["data"]["id"]
        r = client.post(
            f"/api/v1/pipelines/{pid}/trigger",
            headers={"X-Pipeline-Token": "anything"},
            json={"trigger_type": "webhook"},
        )
        assert r.status_code == 401, r.text

    def test_unknown_pipeline_404(self, client, sample_project):
        r = client.post("/api/v1/pipelines/999999/trigger", json={"trigger_type": "webhook"})
        assert r.status_code == 404, r.text


class TestConstantTimeCompareSource:
    """常量时间比较的证据（源码级）"""

    def test_source_uses_compare_digest(self):
        import inspect
        from app.services import pipeline_service as mod

        src = inspect.getsource(mod)
        assert "compare_digest" in src, "token 比对必须使用 hmac.compare_digest（常量时间）"
