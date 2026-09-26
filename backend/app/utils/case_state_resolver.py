"""CaseStateResolver —— 用例状态唯一计算真源（铁律 11/12）

本模块是【唯一】允许计算 Case 状态的地方。Report / Router / 前端统计一律调用
resolve()，禁止各自实现状态判定（禁止绕过）。

设计：
  - status 仅由 execution_steps 真值表推出（铁律 11），不依赖 Execution.status 反推。
  - terminal_reason 由 status + 收敛 context + step 层 error_type/skip_reason 推出
    （铁律 12 六类；incomplete_execution 只作 error_type，不作为 terminal_reason）。
  - 多失败 Step 终态优先级（钉死）：integrity_anomaly > execution_failed >
    business_failure > user_stopped > interrupted。user_stopped/interrupted 仅在
    无任何具体失败证据时生效。
  - status×terminal_reason 合法组合钉死：
      success → normal_success
      failed  → business_failure / user_stopped / interrupted / execution_failed / integrity_anomaly
      skipped → user_stopped / interrupted / execution_failed
      pending/running → NULL
    Resolver 不得产生其他组合。

注意：success+pending → "running"（进行中，实时 UI 需要）；success+skipped →
"failed"（收敛后：部分完成即被跳过 = 未完整执行 incomplete）。
"""

from typing import Optional

from app.utils import terminal_reason as _tr

# ── 步骤状态值域（真值表输入）──
_STEP_STATUSES = frozenset({"success", "failed", "skipped", "pending", "running"})

# ── 具体失败证据分类（error_type 层）──
# 业务断言失败（唯一 business 证据）
_BUSINESS_ERROR_TYPES = frozenset({"business_assertion_failed"})
# 平台/基础设施失败（execution 证据；element 超时引发下游断言失败归因平台）
_EXECUTION_ERROR_TYPES = frozenset({
    "worker_failed", "pre_start_drift",
    "element_not_found", "stale_element", "element_wait_timeout",
    "element_not_interactable", "element_click_intercepted",
    "deadline_exceeded", "circuit_open", "slot_timeout", "quota_timeout",
    "generation_failed", "validation_error", "execution_failed",
})
# 中性证据：incomplete_execution 只推 failed 状态，不参与 reason 推导
_INCOMPLETE = "incomplete_execution"
# 完整性异常标记（收敛时由 Finalizer 写入，防被改写覆盖）
_INTEGRITY_MARKER = "integrity_anomaly"

# status × terminal_reason 合法组合（铁律 12 Pair Invariant，钉死）
_FAILED_VALID_REASONS = frozenset({
    _tr.BUSINESS_FAILURE, _tr.USER_STOPPED, _tr.INTERRUPTED,
    _tr.EXECUTION_FAILED, _tr.INTEGRITY_ANOMALY,
})
_SKIPPED_VALID_REASONS = frozenset({
    _tr.USER_STOPPED, _tr.INTERRUPTED, _tr.EXECUTION_FAILED,
})

# skip_reason → terminal_reason（钉死映射，禁止扩张）
_SKIP_TO_REASON = {
    "user_stopped": _tr.USER_STOPPED,
    "interrupted": _tr.INTERRUPTED,
    "circuit_open": _tr.EXECUTION_FAILED,
    "deadline_exceeded": _tr.EXECUTION_FAILED,
    "worker_failed": _tr.EXECUTION_FAILED,
    "pre_start_drift": _tr.EXECUTION_FAILED,
    "execution_failed": _tr.EXECUTION_FAILED,
}

# exception_type 命中即 execution 证据（failed step 的 exception 为
# TimeoutError/worker/deadline/circuit 类时属 execution_failed，不是 business_failure）
_EXCEPTION_EXECUTION_HINTS = ("timeout", "worker", "deadline", "circuit", "playwright")


def _step_status(s: dict) -> Optional[str]:
    """读取步骤状态；畸形/未知 → None（由调用方判 integrity）"""
    return s.get("status")


def _derive_status(steps: list[dict]) -> str:
    """真值表（铁律 11）：仅由 steps 推出 Case status。

    返回：success / failed / skipped / pending / running / unknown
      unknown = 数据畸形（未识别状态 / 未覆盖组合），收敛时 → failed + integrity_anomaly
    """
    if not steps:
        return "pending"  # 无步骤 = 未执行（Admission 保证非空，防御兜底）
    statuses = set()
    for s in steps:
        st = _step_status(s)
        if st not in _STEP_STATUSES:
            return "unknown"  # 畸形步骤状态
        statuses.add(st)
    has_failed = "failed" in statuses
    has_success = "success" in statuses
    has_skipped = "skipped" in statuses
    has_pending = bool(statuses & {"pending", "running"})
    if has_failed:
        return "failed"  # 真实失败存在，永远最高
    if has_success and has_pending:
        return "running"  # 部分完成、仍在进行（实时 UI）
    if has_success and has_skipped:
        return "failed"  # 收敛后：部分成功即被跳过 = 未完整执行（incomplete）
    if has_success:
        return "success"
    if has_pending:
        return "pending"  # 含未决步骤（pending/running）→ 未终态
    if has_skipped:
        return "skipped"
    return "unknown"  # 未覆盖组合 → 畸形


def _is_integrity(s: dict) -> bool:
    """完整性异常证据（收敛时 Finalizer 写入的标记）"""
    return s.get("error_type") == _INTEGRITY_MARKER


def _is_execution(s: dict) -> bool:
    """平台/基础设施失败证据（含无 error_type 的 failed step 默认归 execution，保守归因）"""
    if s.get("status") != "failed":
        return False
    et = s.get("error_type")
    if et == _INCOMPLETE:
        return False  # 中性：只推 failed 状态，不参与 reason
    if et in _EXECUTION_ERROR_TYPES:
        return True
    if et is None:
        # 无 error_type 的 failed step：exception_type 命中 timeout/worker 等 → execution
        ex = (s.get("exception_type") or "")
        if any(k in ex.lower() for k in _EXCEPTION_EXECUTION_HINTS):
            return True
        return True  # 默认 execution（平台故障不得归咎被测业务）
    return False


def _is_business(s: dict) -> bool:
    """真实业务断言失败证据"""
    return s.get("status") == "failed" and s.get("error_type") in _BUSINESS_ERROR_TYPES


def _skip_reason(s: dict) -> Optional[str]:
    """skipped 步骤的 skip_reason → terminal_reason（钉死映射）"""
    if s.get("status") != "skipped":
        return None
    return _SKIP_TO_REASON.get(s.get("skip_reason"))


def _derive_reason(status: str, steps: list[dict], context: dict) -> Optional[str]:
    """由 status + context + step 层 error_type/skip_reason 推导 terminal_reason。

    context: {"reason": str}，reason ∈ 六类（user_stopped/interrupted/execution_failed/
    business_failure/normal_success/integrity_anomaly）。context 只在该 Case 无具体
    失败证据时兜底（优先级最低，除 business_failure 对 failed 状态的特例）。
    """
    if status == "success":
        return _tr.NORMAL_SUCCESS
    if status in ("pending", "running"):
        return None  # 非终态 → NULL（铁律：pending/running 状态下 terminal_reason=NULL）
    if status == "unknown":
        return _tr.INTEGRITY_ANOMALY

    ctx_reason = context.get("reason") if isinstance(context, dict) else None

    int_evidence = any(_is_integrity(s) for s in steps)
    exec_evidence = any(_is_execution(s) for s in steps)
    bus_evidence = any(_is_business(s) for s in steps)
    user_stop_evidence = any(_skip_reason(s) == _tr.USER_STOPPED for s in steps)
    interrupt_evidence = any(_skip_reason(s) == _tr.INTERRUPTED for s in steps)
    exec_skip_evidence = any(_skip_reason(s) == _tr.EXECUTION_FAILED for s in steps)

    # 多失败 Step 终态优先级（钉死）：integrity > execution_failed > business >
    # user_stopped > interrupted；user_stopped/interrupted 仅无具体失败证据时生效
    if int_evidence and status == "failed":
        return _tr.INTEGRITY_ANOMALY
    if exec_evidence or exec_skip_evidence:
        return _tr.EXECUTION_FAILED
    if bus_evidence or (status == "failed" and ctx_reason == _tr.BUSINESS_FAILURE):
        return _tr.BUSINESS_FAILURE
    if user_stop_evidence or (status != "failed" and ctx_reason == _tr.USER_STOPPED):
        return _tr.USER_STOPPED
    if interrupt_evidence or (status != "failed" and ctx_reason == _tr.INTERRUPTED):
        return _tr.INTERRUPTED

    # Pair Invariant 守卫（钉死）：failed/skipped 只允许各自合法 reason 组合，
    # 非法 context 兜底到保守归因（execution_failed），禁止泄漏 NORMAL_SUCCESS 等
    if status == "failed":
        if ctx_reason in _FAILED_VALID_REASONS:
            return ctx_reason
        return _tr.EXECUTION_FAILED
    if status == "skipped":
        if ctx_reason in _SKIPPED_VALID_REASONS:
            return ctx_reason
        return _tr.EXECUTION_FAILED
    if ctx_reason in _tr.TERMINAL_REASONS:
        return ctx_reason
    return _tr.INTEGRITY_ANOMALY  # 未覆盖 → 完整性异常


def resolve(steps: list[dict], context: Optional[dict] = None) -> tuple[str, Optional[str]]:
    """计算 Case 状态 + terminal_reason（唯一入口）。

    Args:
        steps: ExecutionStep 的 dict 表示（含 status/error_type/skip_reason/exception_type）。
        context: 收敛上下文 {"reason": 六类之一}；实时/非收敛调用可省略。

    Returns:
        (status, terminal_reason)。terminal_reason 仅终态非空；pending/running 为 None。
    """
    ctx = context if isinstance(context, dict) else {}
    status = _derive_status(steps)
    reason = _derive_reason(status, steps, ctx)
    return status, reason


def step_to_dict(step) -> dict:
    """ORM ExecutionStep → resolve() 可消费的 dict（字段缺失安全）"""
    return {
        "status": getattr(step, "status", None),
        "error_type": getattr(step, "error_type", None),
        "skip_reason": getattr(step, "skip_reason", None),
        "exception_type": getattr(step, "exception_type", None),
    }
