"""应用配置测试 — 覆盖 app/config.py 的 Settings 和 load_settings"""

import os
import pytest
from pathlib import Path

from app.config import Settings, load_settings


# ═══════════════════════════════════════════════════════════════════
# Settings 默认值测试
# ═══════════════════════════════════════════════════════════════════

class TestSettingsDefaults:
    """测试 Settings 类的所有默认值。
    需要清除 conftest.py 设置的环境变量，让 pydantic-settings 使用默认值。
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """清除 conftest.py 设置的环境变量，确保测试默认值"""
        env_vars_to_clear = [
            "DATABASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL",
            "PLAYWRIGHT_TIMEOUT", "PLAYWRIGHT_HEADLESS",
            "UPLOAD_DIR", "REPORT_DIR", "SCREENSHOT_DIR", "VIDEO_DIR", "EXCEL_DIR",
            "CORS_ORIGINS", "APP_TITLE", "APP_VERSION", "API_PREFIX",
            "HOST", "PORT", "MAX_HEAL_RETRY", "OPENAI_BASE_URL", "SECRET_KEY",
            "OPENAI_MAX_CALLS_PER_MIN", "HEAL_MAX_RETRY_SAME_ERROR", "PRE_EXECUTION_CHECK",
        ]
        for var in env_vars_to_clear:
            monkeypatch.delenv(var, raising=False)

    def test_default_database_url_is_mysql(self):
        # 验证模型字段默认值（不实例化 Settings，避免 .env 文件干扰）
        field = Settings.model_fields["DATABASE_URL"]
        assert field.default == "mysql+pymysql://root:password@localhost:3306/autopilot"

    def test_default_openai_api_key_is_empty_string(self):
        # 验证模型字段默认值（避免实例化时读取 .env 文件中的真实 Key）
        field = Settings.model_fields["OPENAI_API_KEY"]
        assert field.default == ""

    def test_default_openai_model_is_deepseek(self):
        field = Settings.model_fields["OPENAI_MODEL"]
        assert field.default == "deepseek-chat"

    def test_default_playwright_timeout_is_30000(self):
        s = Settings()
        assert s.PLAYWRIGHT_TIMEOUT == 30_000

    def test_default_playwright_headless_is_true(self):
        s = Settings()
        assert s.PLAYWRIGHT_HEADLESS is True

    def test_default_upload_dir(self):
        s = Settings()
        assert s.UPLOAD_DIR == "./uploads"

    def test_default_report_dir(self):
        s = Settings()
        assert s.REPORT_DIR == "./reports"

    def test_default_screenshot_dir(self):
        s = Settings()
        assert s.SCREENSHOT_DIR == "./uploads/screenshots"

    def test_default_video_dir(self):
        s = Settings()
        assert s.VIDEO_DIR == "./uploads/videos"

    def test_default_excel_dir(self):
        s = Settings()
        assert s.EXCEL_DIR == "./uploads/excels"

    def test_default_cors_origins_includes_localhost_5173(self):
        s = Settings()
        assert "http://localhost:5173" in s.CORS_ORIGINS
        assert "http://127.0.0.1:5173" in s.CORS_ORIGINS
        assert "http://localhost:5174" in s.CORS_ORIGINS
        assert "http://127.0.0.1:5174" in s.CORS_ORIGINS
        assert len(s.CORS_ORIGINS) == 4

    def test_default_app_title(self):
        s = Settings()
        assert s.APP_TITLE == "AutoPilot API"

    def test_default_app_version(self):
        s = Settings()
        assert s.APP_VERSION == "1.0.0"

    def test_default_api_prefix(self):
        s = Settings()
        assert s.API_PREFIX == "/api/v1"

    def test_default_host(self):
        s = Settings()
        assert s.HOST == "0.0.0.0"

    def test_default_port(self):
        s = Settings()
        assert s.PORT == 8000

    def test_default_max_heal_retry(self):
        s = Settings()
        assert s.MAX_HEAL_RETRY == 3

    def test_default_openai_base_url_is_deepseek(self):
        field = Settings.model_fields["OPENAI_BASE_URL"]
        assert field.default == "https://api.deepseek.com/v1"

    def test_default_openai_max_calls_per_min(self):
        field = Settings.model_fields["OPENAI_MAX_CALLS_PER_MIN"]
        assert field.default == 30

    def test_default_heal_max_retry_same_error(self):
        field = Settings.model_fields["HEAL_MAX_RETRY_SAME_ERROR"]
        assert field.default == 3

    def test_default_pre_execution_check(self):
        field = Settings.model_fields["PRE_EXECUTION_CHECK"]
        assert field.default is True


# ═══════════════════════════════════════════════════════════════════
# 环境变量覆盖测试
# ═══════════════════════════════════════════════════════════════════

class TestEnvVarOverride:
    """测试环境变量覆盖 Settings 默认值"""

    def test_env_var_overrides_database_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/mydb")
        s = Settings()
        assert s.DATABASE_URL == "postgresql://user:pass@localhost:5432/mydb"

    def test_env_var_overrides_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-12345")
        s = Settings()
        assert s.OPENAI_API_KEY == "sk-test-key-12345"

    def test_env_var_overrides_openai_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
        s = Settings()
        assert s.OPENAI_MODEL == "gpt-4o-mini"

    def test_env_var_overrides_playwright_timeout(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_TIMEOUT", "60000")
        s = Settings()
        assert s.PLAYWRIGHT_TIMEOUT == 60_000

    def test_env_var_overrides_playwright_headless_false(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")
        s = Settings()
        assert s.PLAYWRIGHT_HEADLESS is False

    def test_env_var_overrides_upload_dir(self, monkeypatch):
        monkeypatch.setenv("UPLOAD_DIR", "/custom/uploads")
        s = Settings()
        assert s.UPLOAD_DIR == "/custom/uploads"

    def test_env_var_overrides_port(self, monkeypatch):
        monkeypatch.setenv("PORT", "9000")
        s = Settings()
        assert s.PORT == 9000

    def test_env_var_overrides_app_title(self, monkeypatch):
        monkeypatch.setenv("APP_TITLE", "Custom API")
        s = Settings()
        assert s.APP_TITLE == "Custom API"

    def test_multiple_env_var_overrides(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("PORT", "9999")
        monkeypatch.setenv("HOST", "127.0.0.1")
        s = Settings()
        assert s.DATABASE_URL == "sqlite:///test.db"
        assert s.PORT == 9999
        assert s.HOST == "127.0.0.1"


# ═══════════════════════════════════════════════════════════════════
# load_settings 函数测试
# ═══════════════════════════════════════════════════════════════════

class TestLoadSettings:
    def test_load_settings_creates_directories(self, mock_file_ops):
        """验证 load_settings() 调用 Path.mkdir 创建目录"""
        import app.config as config_module

        # 重新调用 load_settings，验证目录创建
        # 注意：conftest.py 已经设置好了测试环境变量
        s = config_module.load_settings()
        assert s is not None

        # mock_file_ops 已经 patch 了 Path.mkdir，验证它被调用
        Path.mkdir.assert_called()

    def test_load_settings_warns_when_openai_api_key_empty(self, capsys, mock_file_ops):
        """验证 OPENAI_API_KEY 为空时输出警告"""
        import app.config as config_module

        # 强制设置一个没有 OPENAI_API_KEY 的 Settings
        # 通过 monkeypatch 清除环境变量中的 OPENAI_API_KEY
        # 注意：conftest.py 已经设置了 OPENAI_API_KEY=""
        s = config_module.load_settings()
        assert s is not None

        # 验证警告信息
        captured = capsys.readouterr()
        assert "OPENAI_API_KEY" in captured.out
        assert "Mock" in captured.out

    def test_load_settings_creates_all_required_dirs(self, mock_file_ops):
        """验证为所有必需的目录调用 mkdir"""
        import app.config as config_module

        config_module.load_settings()

        # 验证 6 个目录被创建（5 个配置目录 + "data"）
        assert Path.mkdir.call_count >= 6

    def test_load_settings_mkdir_with_parents(self, mock_file_ops):
        """验证 mkdir 调用使用了 parents=True, exist_ok=True"""
        import app.config as config_module

        config_module.load_settings()

        # 检查 mkdir 调用参数
        for call_args in Path.mkdir.call_args_list:
            kwargs = call_args[1] if len(call_args) > 1 else {}
            assert kwargs.get("parents") is True
            assert kwargs.get("exist_ok") is True


# ═══════════════════════════════════════════════════════════════════
# Settings 边界情况
# ═══════════════════════════════════════════════════════════════════

class TestSettingsEdgeCases:
    def test_settings_config_has_env_file(self):
        s = Settings()
        assert s.model_config.get("env_file") == ".env"
        assert s.model_config.get("env_file_encoding") == "utf-8"

    def test_settings_model_dump(self):
        s = Settings()
        d = s.model_dump()
        assert "DATABASE_URL" in d
        assert "OPENAI_API_KEY" in d
        assert "CORS_ORIGINS" in d
        assert isinstance(d["CORS_ORIGINS"], list)

    def test_secret_key_default(self):
        s = Settings()
        assert s.SECRET_KEY == "change-me-in-production"


# ═══════════════════════════════════════════════════════════════════
# 生产环境安全校验（阶段 10）：APP_ENV=production 时 SECRET_KEY 必须显式配置
# ═══════════════════════════════════════════════════════════════════

class TestProductionSecretKey:
    """生产环境拒绝启动校验：SECRET_KEY 缺失或等于默认值"""

    def test_app_env_alias_sets_env(self, monkeypatch):
        """APP_ENV 别名可识别生产环境（兼容旧名 ENV）"""
        monkeypatch.setenv("APP_ENV", "production")
        assert Settings().ENV == "production"

    def test_legacy_env_alias_still_works(self, monkeypatch):
        """旧名 ENV 仍可识别"""
        monkeypatch.setenv("ENV", "production")
        assert Settings().ENV == "production"

    def test_production_rejects_default_secret_key(self, monkeypatch):
        """APP_ENV=production + SECRET_KEY=默认值 → 拒绝启动（SystemExit）"""
        import app.config as config_module

        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "some-token")
        monkeypatch.delenv("SECRET_KEY", raising=False)  # 回到默认值
        with pytest.raises(SystemExit):
            config_module.load_settings()

    def test_production_accepts_explicit_secret_key(self, monkeypatch):
        """APP_ENV=production + 显式 SECRET_KEY → 正常启动"""
        import app.config as config_module

        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "some-token")
        monkeypatch.setenv("SECRET_KEY", "explicit-production-secret")
        s = config_module.load_settings()
        assert s.ENV == "production"
        assert s.SECRET_KEY == "explicit-production-secret"