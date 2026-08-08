"""Pydantic V2 API 模型 — 按表分文件

每个文件包含：
  - SQLAlchemy ORM 模型（数据库层）
  - Pydantic V2 Schema（API 层）

统一响应格式：{"code":0,"message":"ok","data":{}}
"""

from .project import Project, ProjectCreate, ProjectUpdate, ProjectResponse
from .element import PageElement, PageElementCreate, PageElementUpdate, PageElementResponse
from .test_case import TestCase, TestCaseCreate, TestCaseUpdate, TestCaseResponse
from .generated_code import GeneratedCode, GeneratedCodeCreate, GeneratedCodeResponse
from .execution import Execution, ExecutionCreate, ExecutionResponse
from .execution_step import ExecutionStep, ExecutionStepCreate, ExecutionStepResponse
from .report import Report, ReportCreate, ReportResponse
from .heal_record import HealRecord, HealRecordCreate, HealRecordResponse

__all__ = [
    "Project",
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectResponse",
    "PageElement",
    "PageElementCreate",
    "PageElementUpdate",
    "PageElementResponse",
    "TestCase",
    "TestCaseCreate",
    "TestCaseUpdate",
    "TestCaseResponse",
    "GeneratedCode",
    "GeneratedCodeCreate",
    "GeneratedCodeResponse",
    "Execution",
    "ExecutionCreate",
    "ExecutionResponse",
    "ExecutionStep",
    "ExecutionStepCreate",
    "ExecutionStepResponse",
    "Report",
    "ReportCreate",
    "ReportResponse",
    "HealRecord",
    "HealRecordCreate",
    "HealRecordResponse",
]
