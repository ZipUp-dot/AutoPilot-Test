"""截图工具——Playwright 截图 + 全屏/元素截图"""

from pathlib import Path
from typing import Any

from app.config import settings


class ScreenshotUtil:
    """截图存储工具"""

    @staticmethod
    def path(execution_id: int, case_id: int, step_index: int, stage: str) -> str:
        """生成截图路径: uploads/screenshots/{execution_id}/{case_id}/step_{n}_{before|after}.png"""
        folder = Path(settings.SCREENSHOT_DIR) / str(execution_id) / str(case_id)
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / f"step_{step_index}_{stage}.png")

    @staticmethod
    async def capture(page: Any, filepath: str) -> str:
        """占位——异步截图"""
        return filepath
