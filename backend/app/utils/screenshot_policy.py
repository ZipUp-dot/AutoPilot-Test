"""截图路径策略 — AI 侧截图路径口径钉死

AI 侧截图路径统一写作 `uploads/screenshots/{...}.png`。
服务侧剥掉 `uploads/screenshots/` 前缀后，相对 `settings.SCREENSHOT_DIR` 解析。

越界（非截图子树、../、绝对路径）抛 SecurityError。
禁止把带前缀路径再叠到 UPLOAD_DIR 下（不会产生 uploads/uploads/ 叠加，
因为非 uploads/screenshots/ 开头的路径一律拒绝）。
"""

from pathlib import Path

from app.config import settings
from app.exceptions import SecurityError


class ScreenshotPathPolicy:
    """截图路径解析策略（AI 路径 → 服务侧绝对路径）"""

    # AI 侧路径前缀（统一口径，禁止改写法）
    PREFIX = "uploads/screenshots/"

    @classmethod
    def is_screenshot_path(cls, path: str) -> bool:
        """判断 AI 侧路径是否位于 uploads/screenshots/ 子树"""
        if not path:
            return False
        return path.replace("\\", "/").startswith(cls.PREFIX)

    @classmethod
    def resolve_ai_path(cls, path: str) -> str:
        """解析 AI 侧截图路径 → 服务侧绝对路径。

        - 空路径返回 ""（调用方自行决定默认行为）
        - 必须以 uploads/screenshots/ 开头（反斜杠归一化后校验）
        - 剥掉前缀后相对 settings.SCREENSHOT_DIR resolve
        - 解析结果必须仍位于 SCREENSHOT_DIR 内，否则抛 SecurityError

        Raises:
            SecurityError: 非截图子树 / 越界（../、绝对路径等）
        """
        if not path:
            return ""
        norm = path.replace("\\", "/")
        if not norm.startswith(cls.PREFIX):
            raise SecurityError(
                f"截图路径必须位于 uploads/screenshots/ 子树，当前: {path!r}"
            )
        rel = norm[len(cls.PREFIX):]
        base = Path(settings.SCREENSHOT_DIR).resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base):
            raise SecurityError(f"截图路径越界: {path!r}")
        return str(target)
