"""Tests for app/utils/screenshot.py"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from app.utils.screenshot import ScreenshotUtil


class TestScreenshotPath:
    def test_path_generates_correct_structure(self, mock_settings):
        mock_settings("SCREENSHOT_DIR", "/tmp/test_screenshots")
        result = ScreenshotUtil.path(execution_id=42, case_id=7, step_index=3, stage="before")
        expected = str(Path("/tmp/test_screenshots/42/7/step_3_before.png"))
        assert Path(result) == Path(expected)

    def test_path_after_stage(self, mock_settings):
        mock_settings("SCREENSHOT_DIR", "/tmp/test_screenshots")
        result = ScreenshotUtil.path(execution_id=1, case_id=2, step_index=5, stage="after")
        assert "step_5_after" in str(result)

    def test_path_different_execution(self, mock_settings):
        mock_settings("SCREENSHOT_DIR", "/tmp/test_screenshots")
        r1 = ScreenshotUtil.path(execution_id=1, case_id=1, step_index=1, stage="before")
        r2 = ScreenshotUtil.path(execution_id=2, case_id=1, step_index=1, stage="before")
        assert r1 != r2


class TestScreenshotCapture:
    @pytest.mark.asyncio
    async def test_capture_returns_filepath(self):
        result = await ScreenshotUtil.capture(page=None, filepath="/tmp/test.png")
        assert result == "/tmp/test.png"

    @pytest.mark.asyncio
    async def test_capture_with_mock_page(self):
        mock_page = AsyncMock()
        result = await ScreenshotUtil.capture(page=mock_page, filepath="/tmp/screenshot.png")
        assert result == "/tmp/screenshot.png"