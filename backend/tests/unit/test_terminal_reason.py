"""P0-5 验收 — 全局 terminal_reason 映射（铁律 12 六类，供任务 7/10 复用）

验收 4：全表测试。8 种 error_type → 六类之一，且 6 类值域全覆盖；
incomplete_execution / circuit_open / deadline_exceeded / worker_failed 归入
execution_failed（error_type 细节层，不是独立 terminal_reason）。
"""

import pytest

from app.utils import terminal_reason as t


# 8 组 error_type → 六类 期望值（覆盖全部 6 类）
MAPPING_TABLE = [
    (None, "normal_success"),
    ("success", "normal_success"),            # error_type==success + status=success
    ("business_assertion_failed", "business_failure"),
    ("user_stopped", "user_stopped"),
    ("interrupted", "interrupted"),
    ("incomplete_execution", "execution_failed"),
    ("circuit_open", "execution_failed"),
    ("deadline_exceeded", "execution_failed"),
    ("worker_failed", "execution_failed"),
    ("an_unknown_error_type", "integrity_anomaly"),  # 未知/数据畸形 → integrity_anomaly
]


@pytest.mark.parametrize("error_type,expected", MAPPING_TABLE)
def test_map_terminal_reason_table(error_type, expected):
    assert t.map_terminal_reason(error_type=error_type) == expected


def test_status_success_forced_to_normal():
    # status=success 优先归 normal_success，即使 error_type 为空/存在
    assert t.map_terminal_reason(status="success") == t.NORMAL_SUCCESS
    assert t.map_terminal_reason(status="success", error_type="worker_failed") == t.NORMAL_SUCCESS


def test_six_classes_are_frozen():
    # 六类值域钉死，禁止扩张枚举
    assert t.TERMINAL_REASONS == {
        t.NORMAL_SUCCESS, t.BUSINESS_FAILURE, t.USER_STOPPED,
        t.INTERRUPTED, t.EXECUTION_FAILED, t.INTEGRITY_ANOMALY,
    }
    assert len(t.TERMINAL_REASONS) == 6


def test_error_type_layer_maps_to_execution_failed():
    # 细节层 error_type（铁律 12：execution_failed 涵盖 Worker/Circuit/Deadline/基础设施）
    for et in ("circuit_open", "deadline_exceeded", "worker_failed", "incomplete_execution",
               "generation_failed", "validation_error", "slot_timeout", "quota_timeout"):
        assert t.map_terminal_reason(error_type=et) == t.EXECUTION_FAILED