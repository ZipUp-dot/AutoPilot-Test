"""terminal_reason 全局映射 —— 铁律 12 的六类 + error_type → terminal_reason

terminal_reason 值域仅 6 类（正常/业务/用户停止/中断/执行失败/完整性），禁止扩张枚举。incomplete_execution /
circuit_open / deadline_exceeded / worker_failed 等是 error_type / skip_reason
（细节层），本模块负责把细节层 error_type 归入 6 类，供任务 7/10（Execution 层）复用。

判定优先序（铁律 12）：正常完成→normal_success；业务断言→business_failure；仅因
用户 Stop 未执行→user_stopped；Recovery/进程崩溃→interrupted；Worker 故障 / Circuit /
Deadline / 执行基础设施故障→execution_failed；未知/数据畸形→integrity_anomaly。
"""

from typing import Optional

# 六类值域（铁律 12，钉死，禁止扩张）
NORMAL_SUCCESS = "normal_success"
BUSINESS_FAILURE = "business_failure"
USER_STOPPED = "user_stopped"
INTERRUPTED = "interrupted"
EXECUTION_FAILED = "execution_failed"
INTEGRITY_ANOMALY = "integrity_anomaly"

TERMINAL_REASONS = frozenset({
    NORMAL_SUCCESS, BUSINESS_FAILURE, USER_STOPPED,
    INTERRUPTED, EXECUTION_FAILED, INTEGRITY_ANOMALY,
})

# error_type 归属（细节层 → 六类；摘自铁律 14 注册表 + 生成层层语义）
_BUSINESS = frozenset({"business_assertion_failed"})
_USER_STOPPED_TYPES = frozenset({"user_stopped", "stopped", "recovered_after_stop"})
_INTERRUPTED_TYPES = frozenset({"interrupted"})
_EXECUTION_FAILED_TYPES = frozenset({
    # Execution / Heal 层
    "worker_failed", "pre_start_drift",
    "element_not_found", "stale_element", "element_wait_timeout",
    "element_not_interactable", "element_click_intercepted",
    # Batch / Generation 层（基础设施类）
    "deadline_exceeded", "circuit_open", "slot_timeout", "quota_timeout",
    "generation_failed", "validation_error", "incomplete_execution",
})
_NORMAL_TYPES = frozenset({None, "success", "normal_success"})


def map_terminal_reason(error_type: Optional[str] = None,
                        status: Optional[str] = None) -> str:
    """error_type / status → 六类 terminal_reason 之一。

    Args:
        error_type: 细节层 error_type（无错误/成功为 None 或 "success"）。
        status: （可空）Case 状态；status == "success" 时优先归 normal_success。

    Returns:
        TERMINAL_REASONS 六类之一。
    """
    if status == "success" or error_type in _NORMAL_TYPES:
        return NORMAL_SUCCESS
    if error_type in _BUSINESS:
        return BUSINESS_FAILURE
    if error_type in _USER_STOPPED_TYPES:
        return USER_STOPPED
    if error_type in _INTERRUPTED_TYPES:
        return INTERRUPTED
    if error_type in _EXECUTION_FAILED_TYPES:
        return EXECUTION_FAILED
    # 未知 / 数据畸形 → 完整性异常
    return INTEGRITY_ANOMALY