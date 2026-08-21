"""项目管理业务逻辑"""

import json
import math
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.test_case import TestCase
from app.models.element import PageElement
from app.exceptions import NotFoundException, ValidationException


@dataclass
class PaginatedResult:
    items: list
    total: int
    page: int
    size: int
    pages: int


class ProjectService:
    """项目管理——完整 CRUD"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ── 创建 ──

    def create(self, name: str, target_url: str, test_path: str = "/",
               browser_type: str = "chromium", headless: bool = True,
               platform: str = "web", config_json: Optional[dict] = None) -> Project:
        """创建项目，校验浏览器类型和平台类型"""
        browser_type = browser_type.lower()
        if browser_type not in ("chromium", "firefox", "webkit"):
            raise ValidationException(f"不支持的浏览器类型: {browser_type}，可选 chromium/firefox/webkit")
        if platform not in ("web", "android"):
            raise ValidationException(f"不支持的平台类型: {platform}，可选 web/android")

        project = Project(
            name=name,
            target_url=target_url,
            test_path=test_path,
            browser_type=browser_type,
            headless=1 if headless else 0,
            platform=platform,
            config_json=json.dumps(config_json, ensure_ascii=False) if config_json else "{}",
        )
        self._db.add(project)
        self._db.commit()
        self._db.refresh(project)
        return project

    # ── 查询 ──

    def get_or_404(self, project_id: int) -> Project:
        project = self._db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundException(f"项目 {project_id} 不存在")
        return project

    def list_paginated(self, page: int = 1, size: int = 20) -> PaginatedResult:
        query = self._db.query(Project).order_by(Project.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()

        # 附加用例数量
        result = []
        for p in items:
            case_count = self._db.query(TestCase).filter(TestCase.project_id == p.id).count()
            result.append({
                "id": p.id, "name": p.name, "target_url": p.target_url,
                "platform": p.platform, "status": p.status, "case_count": case_count,
                "created_at": p.created_at, "updated_at": p.updated_at,
            })
        return PaginatedResult(
            items=result, total=total, page=page, size=size,
            pages=math.ceil(total / size) if total > 0 else 0,
        )

    # ── 更新 ──

    def update(self, project_id: int, **kwargs) -> Project:
        project = self.get_or_404(project_id)
        # platform 创建后只读，禁止修改
        kwargs.pop("platform", None)
        if "browser_type" in kwargs and kwargs["browser_type"] is not None:
            bt = kwargs["browser_type"].lower()
            if bt not in ("chromium", "firefox", "webkit"):
                raise ValidationException(f"不支持的浏览器类型: {bt}")
            kwargs["browser_type"] = bt
        if "headless" in kwargs and kwargs["headless"] is not None:
            kwargs["headless"] = 1 if kwargs["headless"] else 0
        # config_json 需要序列化为 JSON 字符串
        if "config_json" in kwargs and kwargs["config_json"] is not None:
            kwargs["config_json"] = json.dumps(kwargs["config_json"], ensure_ascii=False)
        for key, value in kwargs.items():
            if value is not None:
                setattr(project, key, value)
        self._db.commit()
        self._db.refresh(project)
        return project

    # ── 删除（级联） ──

    def delete(self, project_id: int) -> int:
        """级联删除：projects → page_elements, test_cases → generated_codes, executions → execution_steps, execution_reports, heal_records"""
        project = self.get_or_404(project_id)
        self._db.delete(project)
        self._db.commit()
        return project_id
