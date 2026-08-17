# AutoPilot Backend Pytest 测试套件实施计划

## Context

当前测试现状：13 个裸 Python 脚本，无 pytest 框架、无 TestClient、无 DB 隔离、无覆盖率。需要建立完整的 pytest 测试套件，达到语句覆盖率 ≥90%，分支覆盖率 ≥80%，所有外部依赖 Mock，纯 SQLite 内存运行。

## 已有基础设施

- `conftest.py`（项目根目录）：已含 session 级 SQLite 内存引擎、function 级事务回滚、TestClient、Mock Playwright/LLM/Threading 等核心 fixtures
- `tests/factories.py`：已含 8 个 factory-boy 工厂类（需修复 `ExecutionReport` 导入路径 bug）
- 缺少：`tests/__init__.py`、`pytest.ini`

## 实施步骤

### Phase 1：基础设施修复 + 配置 (2 个文件)

1. **修复 `tests/factories.py`** — `ExecutionReport` 导入路径从 `app.models.execution_report` 改为 `app.models.report`；创建 `tests/__init__.py`

2. **创建 `pytest.ini`** — asyncio_mode=auto, coverage 配置, markers 注册

### Phase 2：conftest.py 增强 (1 个文件)

3. **增强 `conftest.py`** — 新增以下 fixtures：
   - `mock_llm_invalid_code` / `mock_llm_network_error` / `mock_llm_retry_then_success` — 覆盖 AI 服务的边界情况
   - `mock_httpx` — 同时 patch ai_service 和 heal_service 的 httpx.Client
   - `mock_file_ops` — patch open/Path.mkdir/os.makedirs/Path.write_text
   - `mock_playwright_for_heal_router` — heal 路由中独立的 Playwright 上下文
   - `mock_jinja_template` — mock report_service 的 Jinja2 模板渲染
   - `sample_project` / `sample_test_case` / `sample_generated_code` / `sample_execution` — 便捷数据 fixtures

### Phase 3：纯函数单元测试 (8 个文件)

4. **`tests/test_models.py`** — 8 个 ORM 模型：创建/字段默认值/JSON 序列化/级联删除/DateTime 默认值
5. **`tests/test_schemas.py`** — 30+ Pydantic 模型：校验/序列化/from_attributes/泛型
6. **`tests/test_config.py`** — Settings 加载/环境变量优先级/目录创建/CORS 解析
7. **`tests/test_exceptions.py`** — 6 个异常类 + 4 个全局处理器响应格式
8. **`tests/test_dependencies.py`** — PaginationParams 计算/paginate()/get_project_or_404
9. **`tests/test_code_validator.py`** — AST 语法校验/黑名单导入/危险内置函数/run_test 检测（15+ 场景）
10. **`tests/test_code_injector.py`** — AST 注入：page.goto/locator.fill/click/expect 全部操作类型 + 注入后代码 AST 可解析 + step_count 正确（12+ 场景）
11. **`tests/test_excel_parser.py`** — 3 种步骤格式解析/中文列名匹配/模糊匹配/动作归一化/优先级归一化/重复检测/文件校验

### Phase 4：Service 层测试 (8 个文件)

12. **`tests/test_project_service.py`** — CRUD/pagination/case_count/update 部分字段/delete 级联
13. **`tests/test_case_service.py`** — 3 种 Excel 导入格式/列表筛选/搜索/详情/删除/批量删除
14. **`tests/test_ai_service.py`** — 元素匹配/智能选择器注入/Prompt 构建/LLM 调用/代码提取/语法校验/安全检查/Mock 模式/批量生成/异常隔离（20+ 场景）
15. **`tests/test_playwright_service.py`** — 执行创建/沙箱执行/MonitorHooks/停止标志/命名空间构建/步骤初始化（15+ 场景）
16. **`tests/test_orchestrator.py`** — 完整流水线/仅生成/仅执行/Mock Service 注入/异常隔离/DB 错误降级/监听报告生成（10+ 场景）
17. **`tests/test_heal_service.py`** — 错误分类/代码提取/代码校验/Prompt 构建/自愈记录管理/重试逻辑/Mock 降级/手动触发（15+ 场景）
18. **`tests/test_report_service.py`** — 数据聚合/状态判定/错误分析/优先级分布/失败选择器排名/HTML 渲染/文件保存/缓存/过期清理/相对路径（15+ 场景）
19. **`tests/test_screenshot.py`** — 路径生成/目录创建

### Phase 5：Router 集成测试 (9 个文件)

20. **`tests/test_router_projects.py`** — CRUD 端点 + 校验 + 404
21. **`tests/test_router_elements.py`** — 抓取/列表/筛选/清空（Mock Playwright）
22. **`tests/test_router_cases.py`** — 导入/列表/搜索/筛选/详情/删除/批量删除（真实 BytesIO Excel）
23. **`tests/test_router_generate.py`** — 单条生成/批量生成/状态轮询/获取最新代码（Mock LLM）
24. **`tests/test_router_executions.py`** — 创建/列表/详情/状态轮询/停止/安全门禁（Mock Playwright + Threading）
25. **`tests/test_router_reports.py`** — 生成/获取信息/缓存（Mock Jinja2 + FileSystem）
26. **`tests/test_router_heal.py`** — 手动触发/记录列表/校验（Mock Playwright + LLM）
27. **`tests/test_middleware_logging.py`** — 请求日志格式
28. **`tests/test_middleware_timing.py`** — X-Process-Time 头

### Phase 6：端到端测试 (1 个文件)

29. **`tests/test_e2e_pipeline.py`** — 完整链路：创建项目 → 导入用例 → 生成代码 → 执行 → 报告（所有外部依赖 Mock）

## Mock 策略总结

| 外部依赖 | Mock 方式 | 适用模块 |
|---|---|---|
| OpenAI API | patch `_call_openai` / `_call_heal_ai` | ai_service, heal_service |
| Playwright | AsyncMock 链 (browser→context→page) | element_service, playwright_service, heal_router |
| 文件系统 | patch `open`/`Path.mkdir`/`Path.write_text` | report_service, case_service |
| 后台线程 | patch `threading.Thread.start` | generate_router, orchestrator, playwright_service |
| Jinja2 模板 | patch `_env.get_template` | report_service |
| Excel 文件 | **不 Mock**，使用真实 openpyxl + BytesIO | case_service, excel_parser |

## 验证方式

```bash
cd backend
pip install pytest pytest-asyncio pytest-cov pytest-mock factory-boy
pytest tests/ -v --cov=app --cov-branch --cov-report=term-missing
```

目标：`--cov-fail-under=80`（分支覆盖率），语句覆盖率 ≥90%