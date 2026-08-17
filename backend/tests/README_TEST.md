# AutoPilot 测试套件运行说明

## 环境要求

- Python 3.10+
- 依赖安装：`pip install -r requirements.txt`

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

| 层级 | 目录 | 说明 | 外部依赖 |
|------|------|------|----------|
| 单元测试 | `tests/unit/` | 基础设施、模型、工具函数 | 纯 Mock |
| 服务层 | `tests/services/` | 业务逻辑服务 | Mock LLM/Playwright |
| 路由层 | `tests/routers/` | API 端点集成 | Mock 所有外部服务 |
| 集成测试 | `tests/integration/` | 端到端业务闭环 | Mock 所有外部依赖 |

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

## 测试文件清单

- `tests/unit/test_config.py` — Pydantic Settings 配置解析
- `tests/unit/test_database.py` — SQLAlchemy 建表 + init() 生命周期
- `tests/unit/test_dependencies.py` — 分页/项目查询依赖注入
- `tests/unit/test_exceptions.py` — 6 个自定义异常 + 全局处理器
- `tests/unit/test_middlewares.py` — Logging/Timing 中间件
- `tests/unit/test_models.py` — 8 个 ORM 模型 + 级联删除
- `tests/unit/test_schemas.py` — 23 个 Pydantic Schema 验证
- `tests/unit/test_utils_excel.py` — Excel 解析（中文列名、3 种步骤格式）
- `tests/unit/test_utils_validator.py` — AST 语法校验 + 安全黑名单
- `tests/unit/test_utils_injector.py` — AST 代码注入 `__monitor_before/after`
- `tests/unit/test_utils_screenshot.py` — 截图路径生成
- `tests/services/test_service_project.py` — Project CRUD
- `tests/services/test_service_ai.py` — LLM 代码生成
- `tests/services/test_service_playwright.py` — 执行引擎 + 沙箱
- `tests/services/test_service_element.py` — 元素抓取 + 7 级选择器
- `tests/services/test_service_orchestrator.py` — 全流水线编排
- `tests/services/test_service_case.py` — Excel 导入 + 用例管理
- `tests/services/test_service_heal.py` — 自愈修复逻辑
- `tests/services/test_service_report.py` — HTML 报告 + 过期清理
- `tests/routers/test_routers_init.py` — router 聚合导入验证
- `tests/routers/test_routers_projects.py` — 项目 CRUD 路由
- `tests/routers/test_routers_elements.py` — 元素抓取/查询路由
- `tests/routers/test_routers_cases.py` — 用例导入/查询路由
- `tests/routers/test_routers_generate.py` — 代码生成路由
- `tests/routers/test_routers_executions.py` — 执行管理路由
- `tests/routers/test_routers_heal.py` — 自愈修复路由
- `tests/routers/test_routers_reports.py` — 报告生成路由
- `tests/integration/test_integration_main.py` — 健康检查/CORS/静态文件/生命周期
- `tests/integration/test_integration_pipeline.py` — 完整 7 步业务闭环 + 异常流水线