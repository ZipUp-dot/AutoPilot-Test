"""测试用例管理业务逻辑 — 导入/查询/删除/批量删除"""

import json
import math
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from sqlalchemy.orm import Session

from app.models.test_case import TestCase
from app.utils.excel_parser import ExcelParser, ParseResult, ParsedCase
from app.exceptions import NotFoundException, ValidationException
from app.config import settings

logger = logging.getLogger("autopilot.case")


@dataclass
class CaseListItem:
    """列表中的用例摘要（只显示前 3 步）"""
    id: int
    project_id: int
    case_name: str
    case_no: Optional[str]
    priority: str
    pre_condition: Optional[str]
    steps_summary: list[dict]
    expected_result: Optional[str]
    source_excel: Optional[str]
    excel_row: Optional[int]
    status: str
    created_at: str
    updated_at: str


@dataclass
class CaseDetail:
    """用例详情（完整 steps）"""
    id: int
    project_id: int
    case_name: str
    case_no: Optional[str]
    priority: str
    pre_condition: Optional[str]
    steps: list[dict]
    expected_result: Optional[str]
    source_excel: Optional[str]
    excel_row: Optional[int]
    status: str
    created_at: str
    updated_at: str


@dataclass
class ImportResult:
    """导入结果"""
    total: int = 0
    success: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)


@dataclass
class PaginatedResult:
    items: list
    total: int
    page: int
    size: int
    pages: int


class CaseService:
    """用例管理——导入/查询/删除/批量删除"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ═══════════════════════════════════════════════
    # 导入
    # ═══════════════════════════════════════════════

    def import_excel(self, project_id: int, file_content: bytes,
                     filename: str = "") -> ImportResult:
        """从 Excel 批量导入用例

        Steps:
          1. 保存文件到 uploads/excels/{project_id}/{timestamp}_{filename}
          2. 调用 ExcelParser 解析
          3. 逐条校验 + 查重 + 批量插入
        """
        # 保存文件
        saved_path = _save_upload(file_content, filename, project_id)

        # 解析
        parse_result = ExcelParser.parse(file_content, filename)
        if parse_result.failed == parse_result.total_rows and not parse_result.cases:
            return ImportResult(
                total=parse_result.total_rows,
                success=0,
                failed=parse_result.failed,
                errors=parse_result.errors,
            )

        # 批量插入
        insert_count = 0
        for case in parse_result.cases:
            try:
                # 查重（同项目同编号）
                if case.case_no:
                    existing = (
                        self._db.query(TestCase)
                        .filter(TestCase.project_id == project_id, TestCase.case_no == case.case_no)
                        .first()
                    )
                    if existing:
                        parse_result.errors.append({
                            "row": case.row_number,
                            "reason": f"编号 {case.case_no} 已存在，已跳过",
                        })
                        parse_result.failed += 1
                        parse_result.success -= 1
                        continue

                # 序列化 steps
                steps_json = json.dumps(
                    [{
                        "step_number": j + 1,
                        "action": s.action,
                        "target": s.target,
                        "value": s.value,
                        "description": s.description,
                    } for j, s in enumerate(case.steps)],
                    ensure_ascii=False,
                )

                self._db.add(TestCase(
                    project_id=project_id,
                    case_name=case.case_name,
                    case_no=case.case_no,
                    priority=case.priority,
                    pre_condition=case.pre_condition,
                    steps=steps_json,
                    expected_result=case.expected_result,
                    source_excel=filename,
                    excel_row=case.row_number,
                    status="imported",
                ))
                insert_count += 1
            except Exception as e:
                parse_result.errors.append({
                    "row": case.row_number,
                    "reason": f"插入失败: {str(e)}",
                })
                parse_result.failed += 1
                parse_result.success -= 1

        self._db.commit()

        return ImportResult(
            total=parse_result.total_rows,
            success=insert_count,
            failed=parse_result.failed,
            errors=parse_result.errors,
        )

    # ═══════════════════════════════════════════════
    # 列表（摘要模式：只显示前 3 步）
    # ═══════════════════════════════════════════════

    def list_paginated(self, project_id: int, page: int = 1, size: int = 20,
                       status: str = None, priority: str = None,
                       keyword: str = None) -> PaginatedResult:
        """分页查询用例列表"""
        query = self._db.query(TestCase).filter(TestCase.project_id == project_id)
        if status:
            query = query.filter(TestCase.status == status)
        if priority:
            query = query.filter(TestCase.priority == priority)
        if keyword:
            kw = f"%{keyword}%"
            query = query.filter(
                (TestCase.case_name.like(kw)) |
                (TestCase.case_no.like(kw)) |
                (TestCase.steps.like(kw))
            )
        query = query.order_by(TestCase.id.desc())

        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()

        result_items = [_to_list_item(case) for case in items]
        return PaginatedResult(
            items=result_items,
            total=total,
            page=page,
            size=size,
            pages=math.ceil(total / size) if total > 0 else 0,
        )

    # ═══════════════════════════════════════════════
    # 详情（完整 steps）
    # ═══════════════════════════════════════════════

    def get_detail(self, project_id: int, case_id: int) -> CaseDetail:
        """获取用例详情"""
        case = (
            self._db.query(TestCase)
            .filter(TestCase.id == case_id, TestCase.project_id == project_id)
            .first()
        )
        if not case:
            raise NotFoundException(f"用例 {case_id} 不存在")
        return _to_detail(case)

    # ═══════════════════════════════════════════════
    # 删除
    # ═══════════════════════════════════════════════

    def delete_one(self, project_id: int, case_id: int) -> int:
        """删除单条用例（级联 generated_codes, execution_steps）"""
        case = (
            self._db.query(TestCase)
            .filter(TestCase.id == case_id, TestCase.project_id == project_id)
            .first()
        )
        if not case:
            raise NotFoundException(f"用例 {case_id} 不存在")
        self._db.delete(case)
        self._db.commit()
        return case_id

    def delete_batch(self, project_id: int, ids: list[int]) -> int:
        """批量删除用例"""
        deleted = (
            self._db.query(TestCase)
            .filter(
                TestCase.project_id == project_id,
                TestCase.id.in_(ids),
            )
            .delete(synchronize_session=False)
        )
        self._db.commit()
        return deleted


# ═══════════════════════════════════════════════
# 数据转换
# ═══════════════════════════════════════════════

def _to_list_item(case: TestCase) -> CaseListItem:
    """转为列表摘要（只含前 3 步）"""
    steps_raw = json.loads(case.steps) if case.steps else []
    summary = steps_raw[:3]
    for s in summary:
        if "description" in s and len(s["description"]) > 60:
            s["description"] = s["description"][:60] + "..."
    return CaseListItem(
        id=case.id,
        project_id=case.project_id,
        case_name=case.case_name,
        case_no=case.case_no,
        priority=case.priority,
        pre_condition=case.pre_condition,
        steps_summary=summary,
        expected_result=case.expected_result,
        source_excel=case.source_excel,
        excel_row=case.excel_row,
        status=case.status,
        created_at=str(case.created_at) if case.created_at else "",
        updated_at=str(case.updated_at) if case.updated_at else "",
    )


def _to_detail(case: TestCase) -> CaseDetail:
    """转为详情（完整 steps）"""
    steps_raw = json.loads(case.steps) if case.steps else []
    return CaseDetail(
        id=case.id,
        project_id=case.project_id,
        case_name=case.case_name,
        case_no=case.case_no,
        priority=case.priority,
        pre_condition=case.pre_condition,
        steps=steps_raw,
        expected_result=case.expected_result,
        source_excel=case.source_excel,
        excel_row=case.excel_row,
        status=case.status,
        created_at=str(case.created_at) if case.created_at else "",
        updated_at=str(case.updated_at) if case.updated_at else "",
    )


def _save_upload(file_content: bytes, filename: str, project_id: int) -> str:
    """保存上传文件到 uploads/excels/{project_id}/{timestamp}_{filename}"""
    ts = int(time.time() * 1000)
    safe_name = f"{ts}_{filename}"
    folder = os.path.join(settings.EXCEL_DIR, str(project_id))
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, safe_name)
    with open(filepath, "wb") as f:
        f.write(file_content)
    logger.info("Excel 文件已保存: %s", filepath)
    return filepath
