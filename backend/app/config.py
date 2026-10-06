"""应用配置 — pydantic-settings 加载环境变量，启动时校验必需配置"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import AliasChoices, Field, ValidationError, model_validator


class Settings(BaseSettings):
    """全局配置，优先级：环境变量 > .env 文件 > 默认值"""

    # ── 应用 ──
    APP_TITLE: str = "AutoPilot API"
    APP_VERSION: str = "1.0.0"
    API_PREFIX: str = "/api/v1"
    SECRET_KEY: str = "change-me-in-production"
    # 运行环境：development / production / test（兼容旧名 APP_ENV）
    ENV: str = Field(default="development", validation_alias=AliasChoices("ENV", "APP_ENV"))
    # 内部 API 令牌：保护 /uploads、/reports 等文件访问接口（无 User/RBAC 的过渡方案）。
    # 生产环境（ENV=production）必须配置，否则启动失败；开发/测试未配置时文件接口放行并告警。
    INTERNAL_API_TOKEN: str = ""

    # ── 数据库 ──
    DATABASE_URL: str = "mysql+pymysql://root:password@localhost:3306/autopilot"

    # ── OpenAI 兼容 AI ──
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.deepseek.com/v1"
    OPENAI_MODEL: str = "deepseek-chat"
    # AI 调用并发上限：同一时刻最多多少个 AI 请求在途（Semaphore 控制）。
    # 根据实际 API 并发限制配置（如 3/5/10），防批量任务无界并发。
    AI_MAX_CONCURRENCY: int = 3
    # AI 调用速率熔断：每分钟最多调用次数（滑动窗口，代码生成 / Vision / 自愈共用）。
    AI_RATE_LIMIT: int = 30
    # 兼容旧配置名（保留）；新配置统一使用 AI_RATE_LIMIT
    OPENAI_MAX_CALLS_PER_MIN: int = 30

    # ── AI HTTP 请求超时（四项分离，禁止使用单项总超时）──
    AI_REQUEST_TIMEOUT_CONNECT: float = 5.0    # 连接
    AI_REQUEST_TIMEOUT_WRITE: float = 10.0     # 写出请求体
    AI_REQUEST_TIMEOUT_READ: float = 120.0     # 读取响应
    AI_REQUEST_TIMEOUT_POOL: float = 5.0       # 连接池排队

    # ── AI 重试 ──
    AI_RETRY_MAX_ATTEMPTS: int = 3             # 单条用例最多 HTTP attempt 次数
    AI_RETRY_BASE: float = 1.0                 # 退避基时 base * 2^(attempt) + jitter(0~0.5s)
    AI_DEADLINE_DEFAULT_SECONDS: float = 120.0 # 未显式传入 case_deadline 时默认 wall-clock 上限

    # ── AI Batch / Case 双层预算（P0-5）──
    # 命名钉死：claim 前检查的量叫 batch_remaining，claim 后的叫 case_remaining。
    AI_BATCH_BUDGET_SECONDS: float = 300.0  # 整批 wall-clock 预算下限（claim 前短路）；实际预算按用例数动态放大（见 batch_generate_service.BatchJob）
    AI_CASE_BUDGET_SECONDS: float = 300.0   # 单 case 预算（claim 后贯穿 quota/slot/HTTP/backoff）

    # ── AI Heal 独立预算（P0-8）──
    # Heal Round 拥有独立 deadline，从 Round claim 时刻起算；Batch Generation 的
    # case_deadline 已过期，禁止复用。Heal 的 quota/slot/HTTP/Retry-After/backoff
    # 全部受该 deadline 约束。
    AI_HEAL_BUDGET_SECONDS: float = 300.0

    @model_validator(mode="after")
    def _compat_ai_rate_limit(self):
        """向后兼容：若仅设置旧名 OPENAI_MAX_CALLS_PER_MIN，AI_RATE_LIMIT 回退沿用旧值"""
        if "AI_RATE_LIMIT" not in self.model_fields_set and "OPENAI_MAX_CALLS_PER_MIN" in self.model_fields_set:
            self.AI_RATE_LIMIT = self.OPENAI_MAX_CALLS_PER_MIN
        return self

    # ── 自愈 ──
    HEAL_MAX_RETRY_SAME_ERROR: int = 3  # 同一 step 同类错误快速失败阈值
    PRE_EXECUTION_CHECK: bool = True     # 执行前目标环境健康检查
    # Heal Skill 开关（诊断基建默认 OFF，禁止默认启用）
    HEAL_SKILL_ENABLED: bool = False
    # 执行心跳超时（秒）：服务重启后 running/healing 任务心跳超过该值视为孤儿
    EXECUTION_HEARTBEAT_TIMEOUT: int = 180

    # ── 报告 claim（P0-10）──
    # 报告 claim 超时阈值（秒）：generating 状态超过该值视为旧 owner 卡死，
    # 允许 reclaim（生成新 claim_token，旧 token 立即失效）。每次 claim 内部最多
    # 一次生成尝试，禁止内部自动无限重试（永久故障置 failed 等待人工/monitor 再触发）。
    REPORT_CLAIM_TIMEOUT_SECONDS: int = 300

    # ── Playwright ──
    PLAYWRIGHT_TIMEOUT: int = 30_000
    PLAYWRIGHT_HEADLESS: bool = True
    MAX_HEAL_RETRY: int = 3

    # ── SSRF 防护 ──
    # 全局 allowlist：执行期除 target 同源外，允许访问的额外 host（逗号分隔）。
    # 支持子域后缀（.corp.internal）；项目级可通过 config_json["allowed_hosts"] 配置。
    SSRF_ALLOWED_HOSTS: str = ""
    # 全局 allowlist 端口：除 80/443 外允许访问的端口（逗号分隔）。
    # 项目级可通过 config_json["allowed_ports"] 配置。
    SSRF_ALLOWED_PORTS: str = ""

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

    # 生产环境必须配置 INTERNAL_API_TOKEN（文件访问受控接口），否则拒绝启动
    if s.ENV == "production" and not s.INTERNAL_API_TOKEN:
        print(
            "[FATAL] ENV=production 必须配置 INTERNAL_API_TOKEN，"
            "否则 /uploads、/reports 文件访问接口无法鉴权。"
        )
        raise SystemExit(1)
    if s.ENV == "production" and s.SECRET_KEY == "change-me-in-production":
        print("[FATAL] ENV=production 必须配置 SECRET_KEY（不能使用默认值）")
        raise SystemExit(1)

    # 确保目录存在
    for d in [s.UPLOAD_DIR, s.REPORT_DIR, s.SCREENSHOT_DIR, s.VIDEO_DIR, s.EXCEL_DIR, "data"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    return s


settings = load_settings()
