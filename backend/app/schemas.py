"""Pydantic Schema — API 请求/响应数据模型"""

from __future__ import annotations
from datetime import datetime
from typing import Any, Generic, TypeVar, Optional
from pydantic import BaseModel, Field

T = TypeVar("T")


# ============================================================
# 统一响应
# ============================================================

class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "ok"
    data: Optional[T] = None


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20
    pages: int = 0


# ============================================================
# Project
# ============================================================

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1)
    target_url: str = Field(..., min_length=1)
    test_path: str = Field(default="/")
    browser_type: str = Field(default="chromium")
    headless: int = Field(default=1, ge=0, le=1)


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    target_url: Optional[str] = None
    test_path: Optional[str] = None
    browser_type: Optional[str] = None
    headless: Optional[int] = Field(default=None, ge=0, le=1)
    status: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    target_url: str
    test_path: str
    browser_type: str
    headless: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListItem(BaseModel):
    id: int
    name: str
    target_url: str
    status: str
    case_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# PageElement
# ============================================================

class PageElementResponse(BaseModel):
    id: int
    project_id: int
    element_type: str
    tag_name: Optional[str]
    element_id: Optional[str]
    name: Optional[str]
    class_name: Optional[str]
    selector: str
    text_content: Optional[str]
    placeholder: Optional[str]
    is_visible: int
    bounding_box: Optional[dict]
    attributes: Optional[dict]
    created_at: datetime

    model_config = {"from_attributes": True}


class CrawlResponse(BaseModel):
    url: str
    elements_found: int
    elements_stored: int
    elapsed_ms: int


# ============================================================
# TestCase
# ============================================================

class TestStep(BaseModel):
    step_number: int
    action: str
    target: str = ""
    value: str = ""
    description: str = ""


class TestCaseResponse(BaseModel):
    id: int
    project_id: int
    case_name: str
    case_no: Optional[str]
    priority: str
    pre_condition: Optional[str]
    steps: list[TestStep]
    expected_result: Optional[str]
    source_excel: Optional[str]
    excel_row: Optional[int]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestCaseImportResult(BaseModel):
    total_rows: int
    imported: int
    skipped: int
    errors: list[str] = []


class BatchDeleteBody(BaseModel):
    ids: list[int] = Field(..., min_length=1)


# ============================================================
# GeneratedCode
# ============================================================

class GeneratedCodeResponse(BaseModel):
    id: int
    case_id: int
    code_content: str
    code_language: str
    generation_prompt: Optional[str]
    ai_model: Optional[str]
    is_valid: int
    syntax_error: Optional[str]
    is_healed: int
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchGenerateResponse(BaseModel):
    batch_id: str
    total: int
    status: str = "pending"  # pending / running / completed / failed


class BatchGenerateStatus(BaseModel):
    batch_id: str
    status: str
    total: int
    completed: int = 0
    failed: int = 0
    progress_pct: float = 0.0


# ============================================================
# Execution
# ============================================================

class ExecutionCreate(BaseModel):
    case_ids: list[int] = Field(..., min_length=1, description="要执行的用例 ID 列表")
    batch_name: Optional[str] = None
    execution_mode: str = Field(default="headless")


class ExecutionResponse(BaseModel):
    id: int
    project_id: int
    batch_name: Optional[str]
    total_cases: int
    passed_cases: int
    failed_cases: int
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    execution_mode: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionStepResponse(BaseModel):
    id: int
    execution_id: int
    case_id: int
    step_index: int
    action: Optional[str]
    target_selector: Optional[str]
    input_value: Optional[str]
    status: str
    screenshot_before: Optional[str]
    screenshot_after: Optional[str]
    log_output: Optional[str]
    error_message: Optional[str]
    duration_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionDetailResponse(ExecutionResponse):
    steps: list[ExecutionStepResponse] = []


class ExecutionStatusResponse(BaseModel):
    execution_id: int
    status: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    total_steps: int
    completed_steps: int
    progress: str = "0/0"
    percentage: int = 0
    current_case: Optional[str] = None
    latest_screenshot: Optional[str] = None


# ============================================================
# Report
# ============================================================

class ReportResponse(BaseModel):
    id: int
    execution_id: int
    report_html: Optional[str]
    report_summary: Optional[dict]
    download_url: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Heal
# ============================================================

class HealRequest(BaseModel):
    case_id: int
    step_index: int


class HealRecordResponse(BaseModel):
    id: int
    execution_step_id: int
    original_code: Optional[str]
    error_context: Optional[dict]
    healed_code: Optional[str]
    heal_prompt: Optional[str]
    retry_status: str
    retry_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Dashboard
# ============================================================

class DashboardStats(BaseModel):
    total_projects: int = 0
    active_projects: int = 0
    total_cases: int = 0
    generated_cases: int = 0
    total_executions: int = 0
    pass_rate: float = 0.0
    heal_success_rate: float = 0.0
