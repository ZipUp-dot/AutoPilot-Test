# AutoPilot 测试套件运行说明

## 环境要求

- Python 3.12+（最低 3.10）
- 依赖安装：`pip install -r requirements-dev.txt`（含 pytest / pytest-asyncio / pytest-cov / pytest-mock 等测试依赖；生产依赖见 `requirements.txt`）

## 运行方式

### 运行全部测试

```bash
cd backend
pytest
```

### 运行指定层级测试

```bash
# 单元测试（基础设施、模型、工具）
pytest tests/unit/

# 服务层测试
pytest tests/services/

# 路由层测试
pytest tests/routers/

# 集成测试（端到端）
pytest tests/integration/
```

### 按标记筛选

```bash
# 仅运行集成测试
pytest -m integration

# 跳过集成测试（快速验证）
pytest -m "not integration"

# 仅运行服务层测试
pytest -m service
```

### 覆盖率报告

```bash
# 终端覆盖率摘要
pytest --cov=app --cov-report=term-missing

# HTML 覆盖率报告
pytest --cov=app --cov-report=html
# 打开 htmlcov/index.html 查看详情
```

## 测试策略

### 4 层架构

| 层级 | 目录 | 文件数 | 说明 | 外部依赖 |
|------|------|------|------|----------|
| 单元测试 | `tests/unit/` | 37 | 基础设施、模型、工具函数、AI 超时/限流、终态语义 | 纯 Mock |
| 服务层 | `tests/services/` | 33 | 业务逻辑服务（生成 / 执行 / 自愈 / 编排 / Admission / Seal） | Mock LLM/Playwright |
| 路由层 | `tests/routers/` | 8 | API 端点集成 | Mock 所有外部服务 |
| 集成测试 | `tests/integration/` | 3 | 端到端业务闭环（含真实 Chromium 金路径，环境开关控制） | Mock 所有外部依赖 |

合计 **81 个测试文件 / 1491 用例**（1489 passed / 2 skipped，覆盖率 90%）。

### Mock 策略

- **LLM API**: `mock_llm` 系列 fixture 模拟 `httpx.Client.post()` 响应
- **Playwright**: `mock_playwright_for_*` 系列 fixture 模拟浏览器/页面/上下文三层链
- **文件系统**: `mock_file_ops` fixture 批量 patch `open`/`Path.mkdir`/`write_text`
- **后台线程**: `block_background_threads` fixture 阻止真实 `threading.Thread` 启动
- **全局状态**: `clear_global_state` autouse fixture 清理 `_batch_jobs`/`_stop_flags`

### 关键约束

- 所有测试运行在 **SQLite 内存数据库**，零外部依赖
- `conftest.py` 在模块级设置环境变量，确保 `pydantic-settings` 单例使用测试配置
- 每个测试函数独立事务，自动回滚，数据完全隔离
- 异步测试使用 `pytest-asyncio`，`asyncio_mode = auto` 无需手动标记

## 测试文件分布

> 完整清单请直接查看目录。以下按层列出**代表性子集**，避免清单随迭代腐化（历史上该清单曾长期滞后于实际目录）。

### 第一层 `tests/unit/`（37 文件）

- `test_config.py` — Pydantic Settings 配置解析
- `test_database.py` / `test_models.py` — SQLAlchemy 建表 + 生命周期、ORM 模型与级联删除
- `test_migration.py` / `test_migration_preflight.py` / `test_alembic_migration.py` — 数据库迁移一致性
- `test_utils_excel.py` / `test_utils_validator.py` / `test_utils_injector.py` / `test_utils_screenshot.py` — 工具层
- `test_utils_appium_injector.py` / `test_validator_android.py` — Android 侧注入与合约校验
- `test_element_locator.py` / `test_platform.py` — 元素定位器与平台隔离
- `test_ai_timeout_no_hang.py` / `test_ai_limiter_semantics.py` / `test_ai_rate_limiter.py` / `test_ai_retry_classification.py` — AI 超时、并发与重试
- `test_terminal_reason.py` / `test_case_state_resolver.py` / `test_kpi_eligibility.py` — 终态语义与指标归因
- `test_security_hardening.py` / `test_utils_safe_playwright.py` / `test_utils_url_policy.py` — 安全加固与沙箱

### 第二层 `tests/services/`（33 文件）

- `test_service_project.py` / `test_service_case.py` / `test_service_element.py` — Project / 用例 / 元素抓取
- `test_service_ai.py` / `test_batch_generate_service.py` — LLM 代码生成与批量生成
- `test_service_playwright.py` / `test_service_appium.py` / `test_service_android_crawl.py` — 双端执行引擎
- `test_service_orchestrator.py` — 全流水线编排 + 平台分发
- `test_service_heal.py` / `test_heal_diagnose.py` / `test_heal_round_guard.py` / `test_heal_finalization.py` / `test_heal_recovery.py` — 自愈链路
- `test_admission_service.py` / `test_execution_seal.py` / `test_manifest_immutable.py` / `test_no_latest_code_backdoor.py` — Admission / Seal / 代码来源钉死
- `test_no_import_in_namespace.py` / `test_validator_rejects_all_imports.py` — 命名空间收口与导入拒绝
- `test_service_report.py` / `test_report_terminal.py` — 报告

### 第三层 `tests/routers/`（8 文件）

`test_routers_{init,projects,elements,cases,generate,executions,heal,reports}.py` — 覆盖全部 API 端点。

### 第四层 `tests/integration/`（3 文件）

- `test_integration_main.py` — 健康检查 / CORS / 静态文件 / 生命周期
- `test_integration_pipeline.py` — 完整 7 步业务闭环 + 异常流水线
- `test_golden_path_real_chromium.py` — 真实 Chromium 金路径（环境开关控制，默认跳过）