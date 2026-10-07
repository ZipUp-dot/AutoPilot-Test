"""AC-01：cron 非法表达式拒绝（422 + 明细）— RED 先行

口径（Spec §11 AC-01 / §6）：
  - 服务层 validate_cron() 对非法表达式抛 ValueError（带明细）；
    is_valid_cron() 返回 False；
  - API 层 POST /projects/{id}/schedules 收到非法 cron → HTTP 422 + message 明细；
  - 6 组坏 cron 参数化（空 / 4 段 / 6 段 / 分钟越界 / 小时越界 / 非 cron 文本）。
"""

import pytest

from app.services.scheduler_service import (
    validate_cron,
    is_valid_cron,
    compute_next_run,
)

# 6 组坏 cron（段数或取值范围非法）
BAD_CRONS = [
    ("", "空表达式"),
    ("* * * *", "段数不足（4 段）"),
    ("* * * * * *", "段数过多（6 段，croniter 默认接受秒段，须显式拒绝）"),
    ("60 * * * *", "分钟越界 60"),
    ("* 25 * * *", "小时越界 25"),
    ("not a cron", "非 cron 文本"),
]

GOOD_CRONS = ["*/5 * * * *", "0 9 * * 1-5", "30 2 1 * *"]


class TestValidateCronUnit:
    """服务层校验（AC-01 单元面）"""

    @pytest.mark.parametrize("expr,reason", BAD_CRONS)
    def test_is_valid_cron_rejects_bad_expr(self, expr, reason):
        assert is_valid_cron(expr) is False, f"{reason}: {expr!r} 应判非法"

    @pytest.mark.parametrize("expr,reason", BAD_CRONS)
    def test_validate_cron_raises_with_detail(self, expr, reason):
        with pytest.raises(ValueError) as exc:
            validate_cron(expr)
        assert str(exc.value).strip(), f"{reason}: 异常须带明细"

    @pytest.mark.parametrize("expr", GOOD_CRONS)
    def test_good_expr_accepted(self, expr):
        assert is_valid_cron(expr) is True
        validate_cron(expr)  # 不抛

    def test_compute_next_run_strictly_future_and_aligned(self):
        from datetime import datetime

        base = datetime(2026, 10, 7, 10, 0, 0)
        nxt = compute_next_run("*/5 * * * *", base)
        assert nxt > base
        assert nxt.minute % 5 == 0


class TestCronRejectedByApi:
    """API 层校验（AC-01 接口面）：非法 cron → 422 + 明细"""

    @pytest.mark.parametrize("expr,reason", BAD_CRONS)
    def test_create_schedule_bad_cron_returns_422(self, client, sample_project, expr, reason):
        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/schedules",
            json={
                "name": "bad-cron",
                "cron_expr": expr,
                "exec_config_json": {"case_ids": [1], "mode": "headless"},
            },
        )
        assert resp.status_code == 422, f"{reason}: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body.get("message"), "422 响应须含明细 message"

    def test_create_schedule_good_cron_ok(self, client, sample_project):
        resp = client.post(
            f"/api/v1/projects/{sample_project.id}/schedules",
            json={
                "name": "nightly",
                "cron_expr": "0 9 * * 1-5",
                "exec_config_json": {"case_ids": [1], "mode": "headless"},
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["cron_expr"] == "0 9 * * 1-5"
        assert data["next_run_at"] is not None
