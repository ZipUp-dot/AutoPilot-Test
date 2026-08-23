"""应用配置 — pydantic-settings 加载环境变量，启动时校验必需配置"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import ValidationError


class Settings(BaseSettings):
    """全局配置，优先级：环境变量 > .env 文件 > 默认值"""

    # ── 应用 ──
    APP_TITLE: str = "AutoPilot API"
    APP_VERSION: str = "1.0.0"
    API_PREFIX: str = "/api/v1"
    SECRET_KEY: str = "change-me-in-production"

    # ── 数据库 ──
    DATABASE_URL: str = "mysql+pymysql://root:password@localhost:3306/autopilot"

    # ── OpenAI 兼容 AI ──
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.deepseek.com/v1"
    OPENAI_MODEL: str = "deepseek-chat"

    # ── Playwright ──
    PLAYWRIGHT_TIMEOUT: int = 30_000
    PLAYWRIGHT_HEADLESS: bool = True
    MAX_HEAL_RETRY: int = 3

    # ── Appium ──
    APPIUM_URL: str = "http://localhost:4723"
    APPIUM_TIMEOUT: int = 30_000

    # ── 存储路径 ──
    UPLOAD_DIR: str = "./uploads"
    REPORT_DIR: str = "./reports"
    SCREENSHOT_DIR: str = "./uploads/screenshots"
    VIDEO_DIR: str = "./uploads/videos"
    EXCEL_DIR: str = "./uploads/excels"

    # ── 服务器 ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def load_settings() -> Settings:
    """加载配置，校验必需项"""
    try:
        s = Settings()
    except ValidationError as e:
        print(f"[FATAL] 配置加载失败，请检查 .env 文件:\n{e}")
        raise SystemExit(1)

    # 校验必需配置
    if not s.OPENAI_API_KEY:
        print("[WARN] OPENAI_API_KEY 未配置，AI 功能将使用 Mock 模式")

    # 确保目录存在
    for d in [s.UPLOAD_DIR, s.REPORT_DIR, s.SCREENSHOT_DIR, s.VIDEO_DIR, s.EXCEL_DIR, "data"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    return s


settings = load_settings()
