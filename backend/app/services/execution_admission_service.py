"""ExecutionAdmissionService — 统一 Admission 入口（Manifest + Runtime + Step 物化）

四种入口闭合（代码来源钉死）：
  - Full Pipeline  → batch_id：Batch Finalized 前置 + 全集合 all-or-none
  - Execute Only/Manual → effective_code（按当前 TestCase hash 调 get_effective_code）
  - Retry → retry_from_execution_id 的 runtime_state[case_id].active_code_id
batch_id 与 retry_from_execution_id 同时非空 → ValidationException（来源歧义）。

admit() 逐项校验 10 项（禁止合并跳过）；任一失败 → 逐 case 返回失败原因、ok=False、
不创建 Execution。materialize() 在【单事务】内落 Execution(queued) + manifest_json +
runtime_state_json(每 case active_code_id=original_code_id) + 全部 ExecutionStep。
禁止「先 commit 再置 queued」的两步写法。

Manifest.cases 为数组，per-case 元素显式含：
  case_id / case_name / priority / step_count / steps_hash / original_code_id
（不建 ExecutionCase 表，per-case 快照完全由 Manifest 承载。）
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.exceptions import ValidationException, NotFoundException
from app.utils.code_validator import CodeValidator, get_effective_code
from app.utils.step_canonicalizer import hash_steps

logger = logging.getLogger("autopilot.admission")


@dataclass
class AdmissionResult:
    """Admission 结果：全集合 all-or-none。ok=False 时 errors 逐 case 承载失败原因。

    P1-1：以下环境快照在 admit() 中从 Admission 时刻的 Project 冻结，
    materialize() 原样写入 Manifest（此后禁止用 Project 当前值覆盖）：
      - execution_mode：本次执行的模式（headless / headed），由调用方显式传入
      - browser_type / target_url / test_path：Project 快照
      - ssrf_policy：project.config_json 的 allowed_hosts / allowed_ports 快照
    """

    ok: bool
    project_id: int
    case_ids: list[int]
    manifest: list[dict] = field(default_factory=list)
    runtime_state: dict = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    execution_mode: str = "headless"
    browser_type: str = "chromium"
    target_url: str = ""
    test_path: str = "/"
    ssrf_policy: dict = field(default_factory=dict)


class ExecutionAdmissionService:
    """Execution Admission 统一入口 + Manifest/Runtime/Step 单事务物化"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 入口
    # ═══════════════════════════════════════════════

    def admit(
        self,
        project_id: int,
        case_ids: list[int],
        batch_id: Optional[str] = None,
        retry_from_execution_id: Optional[int] = None,
        execution_mode: str = "headless",
    ) -> AdmissionResult:
        """执行 Admission：全集合校验 + 代码来源冻结 + 环境快照冻结 + 生成 Manifest/Runtime。

        任一校验失败 → AdmissionResult(ok=False, errors=逐 case 原因)，不建 Execution。

        P1-1：execution_mode 必须由调用方（Router→Orchestrator）显式传入；
        browser_type / target_url / test_path / ssrf_policy 在此从 Project 冻结
        为快照，随 materialize 同一事务写入 Manifest。
        """
        # 来源歧义：batch_id 与 retry_from 同传 → 拒绝
        if batch_id is not None and retry_from_execution_id is not None:
            raise ValidationException(
                "Execution 代码来源歧义：batch_id 与 retry_from_execution_id 不可同时传入"
            )

        result = AdmissionResult(ok=True, project_id=project_id, case_ids=list(case_ids))

        # 1) project 存在
        from app.models.project import Project
        project = self._db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            raise NotFoundException(f"项目 {project_id} 不存在")

        # 3) case_id 无重复（重复 → 整体拒绝，来源歧义）
        dupes = _find_duplicates(case_ids)
        if dupes:
            for cid in dupes:
                result.errors[cid] = "case_id 重复"
            result.ok = False
            return result

        # 预加载 case 行（2 + 4 判定）
        from app.models.test_case import TestCase
        case_rows = {
            r.id: r
            for r in self._db.query(TestCase)
            .filter(TestCase.id.in_(case_ids), TestCase.project_id == project_id)
            .all()
        }

        # 2) 所有 case 存在 + 4) 所有 case 属于该 project（一次性判定）
        missing_or_foreign = [c for c in case_ids if c not in case_rows]
        if missing_or_foreign:
            for cid in missing_or_foreign:
                result.errors[cid] = "用例不存在或不属于该项目"
            result.ok = False
            return result

        platform = getattr(project, "platform", "web") or "web"

        # P1-1：Admission 时刻冻结环境快照（此后执行/Heal 只读 Manifest，禁止回读 Project）
        result.execution_mode = execution_mode
        result.browser_type = (getattr(project, "browser_type", None) or "chromium").strip() or "chromium"
        result.target_url = (project.target_url or "").strip()
        result.test_path = (project.test_path or "/").strip() or "/"
        try:
            cfg = json.loads(project.config_json) if project.config_json else {}
        except (TypeError, ValueError):
            cfg = {}
        result.ssrf_policy = {
            "allowed_hosts": list(cfg.get("allowed_hosts") or []),
            "allowed_ports": list(cfg.get("allowed_ports") or []),
        }

        # 5/6/7）每 case 步骤校验与解析
        steps_map: dict[int, list[dict]] = {}
        for cid in case_ids:
            case = case_rows[cid]
            try:
                steps = json.loads(case.steps) if case.steps else None
            except (TypeError, ValueError):
                steps = None
            if not steps:
                result.errors[cid] = "步骤为空或 JSON 非法"
                continue
            structural_error = _check_structure(steps)
            if structural_error:
                result.errors[cid] = f"步骤结构不完整: {structural_error}"
                continue
            dup_idx = _duplicate_step_index(steps)
            if dup_idx is not None:
                result.errors[cid] = f"步骤序号重复: {dup_idx}"
                continue
            steps_map[cid] = steps

        if not steps_map or len(steps_map) != len(case_ids):
            result.ok = False
            return result

        # 8/9/10）确定 code_id（入口分派）+ 约束校验 → 冻结 [cid -> code_id]
        frozen: dict[int, int] = {}
        code_errors = self._resolve_and_freeze(
            case_ids, batch_id, retry_from_execution_id,
            platform, steps_map, case_rows, frozen,
        )
        if code_errors:
            for cid, reason in code_errors.items():
                result.errors[cid] = reason
            result.ok = False
            return result

        # 全通过 → 冻结 Manifest + Runtime（单一真源：frozen）
        for cid in case_ids:
            case = case_rows[cid]
            steps = steps_map[cid]
            code_id = frozen[cid]
            result.manifest.append({
                "case_id": cid,
                "case_name": case.case_name,
                "priority": case.priority or "P1",
                "step_count": len(steps),
                "steps_hash": hash_steps(steps),
                "original_code_id": code_id,
            })
            result.runtime_state[str(cid)] = {"active_code_id": code_id}

        return result

    # ═══════════════════════════════════════════════
    # 物化（单事务）
    # ═══════════════════════════════════════════════

    def materialize(
        self,
        result: AdmissionResult,
        batch_name: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> int:
        """单事务落 Execution(queued) + Manifest + Runtime + 全部 ExecutionStep。

        原子性：COMMIT 前任何失败 rollback → 什么都不存在，无半成品。
        queued 的 Execution 必然携带完整 Manifest/Runtime/Steps。

        P1-1：execution_mode 唯一来源是 result（Admission 冻结值）；mode 形参仅
        兼容旧调用兜底，不得覆盖 Manifest 中已冻结的 execution_mode。
        """
        if not result.ok:
            raise ValidationException("无法物化失败的 Admission")

        from app.models.execution import Execution
        from app.models.execution_step import ExecutionStep
        from app.models.test_case import TestCase
        from app.services.execution_state import generate_worker_id
        from datetime import datetime

        execution = Execution(
            project_id=result.project_id,
            batch_name=batch_name,
            total_cases=len(result.case_ids),
            execution_mode=result.execution_mode or mode or "headless",
            status="queued",
            start_time=datetime.utcnow(),
            heartbeat_at=datetime.utcnow(),
            worker_id=generate_worker_id(),
            progress=0,
        )
        # P0-10：Manifest 加 project 快照（name/target_url）——报告环境/名称的
        # 事实源；Admission 时冻结，此后改 Project 不影响既有 Execution 的报告。
        # P1-1：Manifest 顶层冻结 execution 期环境（target_url/test_path/browser_type/
        # execution_mode/ssrf_policy），执行/Heal 只读此处，禁止回读 Project。
        manifest = {
            "schema_version": 1,
            "cases": result.manifest,
            "target_url": result.target_url,
            "test_path": result.test_path,
            "browser_type": result.browser_type,
            "execution_mode": result.execution_mode,
            "ssrf_policy": result.ssrf_policy,
        }
        from app.models.project import Project as _Project
        proj = (
            self._db.query(_Project)
            .filter(_Project.id == result.project_id)
            .first()
        )
        if proj is not None:
            manifest["project"] = {
                "name": proj.name,
                "target_url": proj.target_url or "",
                "test_path": proj.test_path or "/",
            }
        execution.manifest_json = json.dumps(manifest, ensure_ascii=False)
        execution.runtime_state_json = json.dumps(result.runtime_state, ensure_ascii=False)
        self._db.add(execution)
        self._db.flush()  # 获取 execution.id，仍在同一事务内

        for cid in result.case_ids:
            case = self._db.query(TestCase).filter(TestCase.id == cid).first()
            if not case or not case.steps:
                continue
            try:
                steps = json.loads(case.steps)
            except (TypeError, ValueError):
                continue
            for s in steps:
                self._db.add(ExecutionStep(
                    execution_id=execution.id,
                    case_id=cid,
                    step_index=s.get("step_number", 1),
                    action=s.get("action", ""),
                    target_selector=s.get("target", ""),
                    input_value=s.get("value", ""),
                    assertion=s.get("assertion") or s.get("expected_result") or "",
                    status="pending",
                ))

        self._db.commit()
        self._db.refresh(execution)
        return execution.id

    # ═══════════════════════════════════════════════
    # 内部：code 来源分派 + 9/10 校验 + 冻结
    # ═══════════════════════════════════════════════

    def _resolve_and_freeze(
        self,
        case_ids: list[int],
        batch_id: Optional[str],
        retry_from_execution_id: Optional[int],
        platform: str,
        steps_map: dict[int, list[dict]],
        case_rows: dict[int, object],
        frozen: dict[int, int],
    ) -> dict[int, str]:
        """按入口分派 code_id、执行 9/10 校验，把通过的 code_id 写入 frozen。

        Returns {case_id: reason}；frozen 仅在成功时填充。
        candidates = {case_id: GeneratedCode|None}，reasons = top-level 整批拒绝原因。
        """
        if retry_from_execution_id is not None:
            candidates, chosen, reasons = self._retry_candidates(
                retry_from_execution_id, case_ids)
        elif batch_id is not None:
            candidates, chosen, reasons = self._batch_candidates(batch_id, case_ids)
        else:
            candidates = {cid: None for cid in case_ids}
            chosen: dict[int, object] = {}
            reasons: dict[int, str] = {}
            for cid in case_ids:
                case = case_rows[cid]
                current_hash = None
                if case.steps:
                    try:
                        current_hash = hash_steps(json.loads(case.steps))
                    except (TypeError, ValueError):
                        current_hash = None
                candidates[cid] = (
                    get_effective_code(self._db, cid, current_hash) if current_hash else None
                )

        if reasons:  # 整批/顶层拒绝（batch all-or-none 全集合不满足）
            return dict(reasons)
        if chosen:  # 该 case 候选代码已由入口钉死（batch code_id / retry active_code_id）
            for cid, code in chosen.items():
                candidates[cid] = code

        errors: dict[int, str] = {}
        for cid in case_ids:
            code = candidates.get(cid)
            reason = self._verify_code(code, cid, platform, steps_map)
            if reason is not None:
                errors[cid] = reason
                continue
            frozen[cid] = code.id
        return errors

    def _batch_candidates(self, batch_id, case_ids) -> tuple:
        """batch 入口：Batch Finalized 前置 + 全集合 all-or-none。

        任一前置不满足 → reasons 非空（整批拒绝）；否则返回 (candidates, chosen, {})。
        """
        from app.models.batch_records import BatchRecord
        from app.models.batch_cases import BatchCase
        from app.models.generated_code import GeneratedCode

        record = (
            self._db.query(BatchRecord)
            .filter(BatchRecord.batch_id == batch_id)
            .first()
        )
        if record is None or record.batch_status != "completed":
            return {}, {}, {cid: "批次未完成最终化，禁止 Admission" for cid in case_ids}

        batch_cases = (
            self._db.query(BatchCase)
            .filter(BatchCase.batch_id == batch_id)
            .all()
        )
        batch_case_ids = {bc.case_id for bc in batch_cases}

        # 全集合 all-or-none：case_ids 必须与 Batch 集合完全一致（禁止子集偷跑）
        if set(case_ids) != batch_case_ids:
            return {}, {}, {cid: "case_ids 与批次集合不一致（全集合 all-or-none）" for cid in case_ids}

        by_case = {bc.case_id: bc for bc in batch_cases}
        # 任一 BatchCase 非 success / mock / code_id 空 → 整个批次拒绝
        bad = [
            cid for cid in case_ids
            if by_case.get(cid) is None
            or by_case[cid].status != "success"
            or by_case[cid].is_mock_at_attempt
            or not by_case[cid].code_id
        ]
        if bad:
            return {}, {}, {cid: "批次快照存在不可执行用例，整个批次拒绝 Admission" for cid in case_ids}

        chosen = {
            cid: self._db.query(GeneratedCode)
            .filter(GeneratedCode.id == by_case[cid].code_id).first()
            for cid in case_ids
        }
        return {}, chosen, {}

    def _retry_candidates(self, retry_from_execution_id, case_ids) -> tuple:
        """retry 入口：从源 Execution runtime_state[case_id].active_code_id 取码。"""
        from app.models.execution import Execution
        from app.models.generated_code import GeneratedCode

        source = (
            self._db.query(Execution)
            .filter(Execution.id == retry_from_execution_id)
            .first()
        )
        if source is None:
            return {}, {}, {cid: f"源执行 {retry_from_execution_id} 不存在" for cid in case_ids}
        try:
            runtime_state = json.loads(source.runtime_state_json) if source.runtime_state_json else {}
        except (TypeError, ValueError):
            runtime_state = {}

        chosen: dict[int, object] = {}
        candidates: dict[int, object] = {}
        for cid in case_ids:
            entry = runtime_state.get(str(cid))
            active_code_id = entry.get("active_code_id") if entry else None
            if not active_code_id:
                candidates[cid] = None  # _verify_code 报「code_id 未确定」
                continue
            chosen[cid] = self._db.query(GeneratedCode).filter(GeneratedCode.id == active_code_id).first()
            candidates[cid] = chosen[cid]
        return candidates, chosen, {}

    def _verify_code(
        self, code, cid: int, platform: str,
        steps_map: dict[int, list[dict]],
    ) -> Optional[str]:
        """9/10 校验。返回失败原因或 None。"""
        current_hash = hash_steps(steps_map[cid])
        if code is None:
            return "code_id 未确定（无有效代码）"
        # 9) code 当前 Validator 有效、非 mock
        if code.is_mock:
            return "代码为 Mock 生成，禁止执行"
        error = CodeValidator.validate(code.code_content, platform=platform)
        if error:
            return f"代码未通过当前校验: {error}"
        if code.is_valid != 1:
            return "代码未标记为有效"
        # 10) code.source_steps_hash == 当前 TestCase hash_steps
        if not code.source_steps_hash or code.source_steps_hash != current_hash:
            return "代码来源步骤与当前用例步骤不一致"
        return None


def _find_duplicates(seq: list[int]) -> list[int]:
    seen = set()
    dupes = set()
    for x in seq:
        if x in seen:
            dupes.add(x)
        seen.add(x)
    return sorted(dupes)


def _check_structure(steps: list[dict]) -> Optional[str]:
    """6) step 结构完整：每个 step 为 dict，且含非空 action 与 step_number。"""
    for idx, s in enumerate(steps):
        if not isinstance(s, dict) or not s:
            return f"第 {idx + 1} 步为空对象"
        action = s.get("action")
        if not action or not str(action).strip():
            return f"第 {idx + 1} 步缺少 action"
        if "step_number" not in s:
            return f"第 {idx + 1} 步缺少 step_number"
    return None


def _duplicate_step_index(steps: list[dict]) -> Optional[int]:
    """7) step_index（step_number）无重复。冲突时报冲突的序号。"""
    seen: set[int] = set()
    for s in steps:
        idx = s.get("step_number", 1)
        if idx in seen:
            return int(idx)
        seen.add(idx)
    return None