"""ExecutionCodeResolver — Execution/Heal/Report 统一代码解析入口

只读 Execution.runtime_state_json[case_id].active_code_id 定位冻结代码
（Admission 时落定，Manifest immutable）。禁止回退 latest / get_effective_code。

语义：Execution 已物化后，运行期代码来源唯一由 runtime_state 的
active_code_id 决定。active_code_id == Manifest.cases[case_id].original_code_id
（Admission 时冻结），运行期不再重选码。
"""

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("autopilot.exec_code_resolver")


class ExecutionCodeResolver:
    """运行期活跃代码解析器（读 runtime_state[case_id].active_code_id）"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_active_code(self, execution_id: int, case_id: int):
        """返回该 Execution 下 case_id 的冻结 GeneratedCode 行；无 → None。"""
        from app.models.execution import Execution
        from app.models.generated_code import GeneratedCode

        execution = (
            self._db.query(Execution)
            .filter(Execution.id == execution_id)
            .first()
        )
        if execution is None or not execution.runtime_state_json:
            return None

        try:
            runtime_state = json.loads(execution.runtime_state_json)
        except (TypeError, ValueError):
            logger.error("runtime_state_json 解析失败: execution_id=%s", execution_id)
            return None

        entry = runtime_state.get(str(case_id))
        active_code_id = entry.get("active_code_id") if entry else None
        if not active_code_id:
            return None

        code = (
            self._db.query(GeneratedCode)
            .filter(GeneratedCode.id == active_code_id)
            .first()
        )
        return code

    def get_active_code_id(self, execution_id: int, case_id: int) -> Optional[int]:
        """返回冻结的 active_code_id；无 → None。"""
        if self._db is None:
            # 纯读取（测试环境可能注入 None session），直接解析 Execution 行
            return None
        code = self.get_active_code(execution_id, case_id)
        return code.id if code else None