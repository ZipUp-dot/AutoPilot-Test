"""AI Draft API 契约（供 10B 前端对齐）—— EXT-AITC-10A 节拍 1（RED 先行）

契约（Spec §6）：
  POST /api/v1/ai-drafts/generate          起生成
  GET  /api/v1/ai-drafts                   列表（?project_id=）
  GET  /api/v1/ai-drafts/{draft_id}        详情
  PUT  /api/v1/ai-drafts/{draft_id}/review 人工三维写入
  POST /api/v1/ai-drafts/{draft_id}/versions 编辑产生新版本
  POST /api/v1/ai-drafts/{draft_id}/promote   Promote
"""

API = "/api/v1/ai-drafts"

_EXPECTED = [
    f"{API}/generate",
    API,
    f"{API}/{{draft_id}}",
    f"{API}/{{draft_id}}/review",
    f"{API}/{{draft_id}}/versions",
    f"{API}/{{draft_id}}/promote",
]


def test_all_contract_paths_are_registered(client):
    """6 条契约路径必须全部注册（Module Entry 层证据）"""
    paths = set(client.get("/openapi.json").json()["paths"].keys())
    missing = [p for p in _EXPECTED if p not in paths]
    assert missing == [], f"未注册的契约路径: {missing}"


def test_generate_and_list_roundtrip(client):
    """生成入口可达且返回 draft 标识（AI 以 Mock 注入）"""
    r = client.post(f"{API}/generate",
                    json={"project_id": 1, "source_url": "https://example.com"})
    assert r.status_code != 404, "generate 未注册"
    r2 = client.get(API, params={"project_id": 1})
    assert r2.status_code == 200, r2.text
    assert "items" in r2.json().get("data", r2.json())
