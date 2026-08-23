# AutoPilot Backend

> AI 驱动的自动化测试平台 — 后端服务

基于 FastAPI + SQLAlchemy + Playwright 构建，提供项目管理、元素抓取、Excel 用例导入、AI 代码生成、Playwright 执行、自愈修复、HTML 报告生成等完整测试能力。

---

## 技术栈

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 |
| 数据库驱动 | PyMySQL 1.1 |
| 数据库 | MySQL 8.0 |
| 浏览器自动化 | Playwright 1.47（Chromium 无头模式） |
| 移动端自动化 | Appium Python Client 4.2（UiAutomator2） |
| AI 代码生成 | OpenAI 兼容 API（DeepSeek / gpt-4o，支持 Vision 截图分析 + Mock 模式） |
| HTTP 客户端 | httpx 0.27 |
| 数据校验 | Pydantic 2.9 + pydantic-settings 2.5 |
| 异步 | asyncio + threading |
| Excel 解析 | openpyxl 3.1 |
| 报告模板 | Jinja2 3.1 |
| 文件上传 | python-multipart + aiofiles |
| 运行环境 | Python 3.12+ |

---

## 项目结构

```
backend/
├── app/
│   ├── main.py                  # FastAPI 入口，生命周期管理
│   ├── config.py                # 配置加载（pydantic-settings）
│   ├── dependencies.py          # 依赖注入（get_db 等）
│   ├── exceptions.py            # 全局异常处理器
│   ├── schemas.py               # Pydantic 响应模型（含 platform + config_json）
│   ├── db/
│   │   ├── database.py          # SQLAlchemy 引擎 + Session 工厂
│   │   └── schema.sql           # 数据库建表脚本
│   ├── models/                  # ORM 模型（9 张表）
│   │   ├── project.py           # 项目表（含 platform + config_json）
│   │   ├── element.py           # 页面元素表（含 platform + selector_type + metadata）
│   │   ├── test_case.py         # 测试用例表
│   │   ├── test_step.py         # 测试步骤（已合并到 test_case 的 JSON 字段）
│   │   ├── generated_code.py    # 生成代码表
│   │   ├── execution.py         # 执行批次表
│   │   ├── execution_step.py    # 执行步骤表（含 exception_type）
│   │   ├── report.py            # 执行报告表
│   │   └── heal_record.py       # 自愈记录表（含 attempts JSON 数组）
│   ├── routers/                 # API 路由（8 个）
│   │   ├── projects.py          # 项目 CRUD（含 platform + config_json）
│   │   ├── elements.py          # 元素抓取 / 列表 / 清空（平台感知）
│   │   ├── cases.py             # 用例导入 / 列表 / 删除
│   │   ├── generate.py          # 代码生成（单条 + 批量，平台感知）
│   │   ├── executions.py        # 执行管理（创建 / 轮询 / 停止，平台分发）
│   │   ├── reports.py           # 报告生成 / 查询
│   │   └── heal.py              # 自愈修复（Web / Android 双分支）
│   │   ├── services/                # 业务逻辑层（10 个服务）
│   │   │   ├── project_service.py   # 项目 CRUD（含 platform 只读保护）
│   │   │   ├── element_service.py   # 元素抓取 + 7 级选择器生成 + AI 辅助导航（平台感知）
│   │   │   ├── case_service.py      # Excel 解析 + 用例管理
│   │   │   ├── ai_service.py        # LLM 调用 + 代码生成 + Vision 截图分析（Web/Android 双平台）
│   │   │   ├── playwright_service.py # Playwright 执行引擎（Web）
│   │   │   ├── appium_service.py    # Appium 执行引擎（Android，同步链式调用）
│   │   │   ├── android_crawl_service.py # Android 元素抓取（Appium）
│   │   │   ├── orchestrator.py      # 执行编排器（平台分发 → 同步/异步线程）
│   │   │   ├── heal_service.py      # 自愈修复（Web/Android 双分支）
│   │   │   └── report_service.py    # HTML 报告生成（含异常类型分类）
│   ├── utils/                   # 工具模块（6 个）
│   │   ├── excel_parser.py      # Excel 智能解析（中文列名）
│   │   ├── code_validator.py    # AST 语法校验 + 安全审计 + 平台合约检查
│   │   ├── code_injector.py     # Web 截图/日志注入（异步）
│   │   ├── appium_code_injector.py # Android 监控注入（同步，独立定义）
│   │   └── screenshot.py        # 截图工具类
│   ├── prompts/                 # AI Prompt 模板（5 个）
│   │   ├── generate_prompt.txt   # Web 代码生成 Prompt
│   │   ├── generate_prompt_android.txt # Android 代码生成 Prompt
│   │   ├── heal_prompt.txt       # Web 自愈修复 Prompt
│   │   ├── heal_prompt_android.txt # Android 自愈修复 Prompt
│   │   └── crawl_analyze.txt     # AI 感知页面抓取分析 Prompt（前置操作判定）
│   ├── templates/                # Jinja2 模板
│   │   └── report_template.html  # HTML 报告模板（内联 CSS/JS/Chart.js）
│   ├── middlewares/              # 中间件
│   │   ├── logging.py            # 请求日志（method/path/status/duration/ip）
│   │   └── timing.py             # 响应时间头
├── tests/                       # pytest 测试套件（4 层架构，780+ 测试）
│   ├── conftest.py              # 共享 Fixture（SQLite 内存库 + 外部依赖 Mock）
│   ├── factories.py             # 工厂类
│   ├── README_TEST.md           # 测试运行说明
│   ├── unit/                    # 第一层：单元测试（14 文件）
│   │   ├── test_config.py
│   │   ├── test_database.py
│   │   ├── test_dependencies.py
│   │   ├── test_exceptions.py
│   │   ├── test_middlewares.py
│   │   ├── test_models.py
│   │   ├── test_utils_excel.py
│   │   ├── test_utils_validator.py
│   │   ├── test_utils_injector.py
│   │   ├── test_utils_screenshot.py
│   │   ├── test_utils_appium_injector.py # Appium 代码注入测试
│   │   ├── test_validator_android.py     # Android 合约校验测试
│   │   ├── test_element_locator.py       # 元素定位器测试
│   │   └── test_platform.py              # 平台隔离测试
│   ├── services/                # 第二层：服务层测试（10 文件）
│   │   ├── test_service_project.py
│   │   ├── test_service_ai.py
│   │   ├── test_service_playwright.py
│   │   ├── test_service_appium.py        # Appium 执行引擎测试
│   │   ├── test_service_element.py
│   │   ├── test_service_orchestrator.py  # 平台分发编排测试
│   │   ├── test_service_case.py
│   │   ├── test_service_heal.py
│   │   ├── test_service_report.py
│   │   └── test_migration.py             # 数据库迁移测试
│   ├── routers/                 # 第三层：路由集成测试（8 文件）
│   │   ├── test_routers_init.py
│   │   ├── test_routers_projects.py
│   │   ├── test_routers_elements.py
│   │   ├── test_routers_cases.py
│   │   ├── test_routers_generate.py
│   │   ├── test_routers_executions.py
│   │   ├── test_routers_heal.py
│   │   └── test_routers_reports.py
│   └── integration/             # 第四层：端到端集成测试（2 文件）
│       ├── test_integration_main.py
│       └── test_integration_pipeline.py
├── data/                        # 数据目录
├── uploads/                     # 上传文件
│   ├── screenshots/             # 执行截图（按 execution_id/case_id 组织）
│   ├── excels/                  # 导入的 Excel 文件
│   └── videos/                  # 视频录制
├── reports/                     # 生成的 HTML 报告（30 天自动清理）
├── _logs/                       # 运行日志
├── .env.example                 # 环境变量模板
├── Dockerfile                   # Docker 构建
├── requirements.txt             # Python 依赖
└── README.md
```

---

## 快速开始

### 1. 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.12+ | 推荐 3.12，最低 3.10 |
| MySQL | 8.0+ | 生产数据库（唯一支持） |
| pip | 24.0+ | Python 包管理器 |
| Playwright | 1.47 | Chromium 浏览器自动化 |

### 2. 创建 Python 虚拟环境（推荐）

```bash
# 进入后端目录
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
```

### 3. 创建数据库

```bash
# 登录 MySQL
mysql -u root -p

# 创建数据库
CREATE DATABASE autopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 退出
exit
```

> 项目仅支持 MySQL 8.0+，SQLite 已移除。数据库连接通过 `.env` 中的 `DATABASE_URL` 配置。

### 4. 安装依赖

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright Chromium 浏览器（约 150MB，仅首次需要）
playwright install chromium
```

> **注意**：如果 `playwright install` 下载慢，可以设置镜像：
> ```bash
> set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
> playwright install chromium
> ```

### 5. 配置环境变量

```bash
# 复制配置文件模板
cp .env.example .env
```

编辑 `.env` 文件，根据你的环境修改：

```env
# ── 应用 ──
SECRET_KEY=change-me-in-production

# ── 数据库 ──
DATABASE_URL=mysql+pymysql://root:你的密码@localhost:3306/autopilot

# ── AI 代码生成（可选）──
# 默认使用 DeepSeek（国内可直连）；不填则使用 Mock 模式，不影响其他功能
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# ── Playwright ──
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT=30000
MAX_HEAL_RETRY=3

# ── 存储路径 ──
UPLOAD_DIR=./uploads
REPORT_DIR=./reports
SCREENSHOT_DIR=./uploads/screenshots
VIDEO_DIR=./uploads/videos
EXCEL_DIR=./uploads/excels

# ── 服务器 ──
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173","http://localhost:5174","http://127.0.0.1:5174"]
```

### 6. 初始化数据库

```bash
# 确保在 backend 目录下
cd backend

# 方式一：启动服务自动初始化（推荐）
# 首次启动会自动执行 schema.sql 建表

# 方式二：手动执行 SQL 脚本
mysql -u root -p autopilot < app/db/schema.sql
```

### 7. 启动服务

> ⚠️ **重要**：`main.py` 启动时自动设置 `TOOLHOST_SANDBOX_DISABLED=true`，这是 Playwright 正常工作的必要条件。**请勿使用 `--reload` 参数**，否则 IDE 沙箱会拦截 Playwright 的子进程调用，导致元素抓取和执行引擎报错。

#### 开发模式

```bash
# 在 backend 目录下执行
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

参数说明：

| 参数 | 说明 |
|------|------|
| `app.main:app` | 模块路径：app/main.py 中的 app 实例 |
| `--host 127.0.0.1` | 监听地址（仅本机访问） |
| `--host 0.0.0.0` | 监听所有网卡（允许局域网访问） |
| `--port 8000` | 监听端口 |
| `--workers 4` | 多进程模式（生产环境） |

> ⚠️ **禁止使用 `--reload`**：热重载模式会导致 IDE 沙箱拦截 Playwright 子进程，引发 `NotImplementedError`。如需热重载，请使用 IDE 自带的重启功能。

#### 生产模式

```bash
# 不带头重载，多 worker
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

#### 后台运行

```bash
# Windows（PowerShell）
Start-Process -NoNewWindow python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"

# macOS / Linux
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
```

### 8. 验证服务

启动成功后，终端会显示：

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     数据库初始化完成
```

验证接口：

```bash
# 健康检查
curl http://localhost:8000/health
# → {"code":0,"message":"ok","data":{"status":"healthy"}}

# 项目列表
curl http://localhost:8000/api/v1/projects/
# → {"code":0,"message":"ok","data":{"items":[],"total":0,...}}

# 浏览器访问 Swagger 文档
# http://localhost:8000/docs
```

### 9. 停止服务

```bash
# 开发模式：在终端按 Ctrl+C

# 查找并结束进程
# Windows:
netstat -ano | findstr :8000
taskkill /PID <进程ID> /F

# macOS / Linux:
lsof -ti:8000 | xargs kill -9
```

### 10. Docker 部署

推荐使用项目根目录的 `docker-compose.yml` 一键启动全部服务（MySQL + Backend + Frontend）：

```bash
# 在项目根目录执行
cd ..
docker compose up -d
```

访问 `http://localhost:8080` 打开前端界面。

#### 单独构建后端镜像

```bash
# 构建镜像
docker build -t autopilot-backend .

# 运行容器（需先启动 MySQL）
docker run -d \
  --name autopilot-backend \
  -p 8000:8000 \
  -e DATABASE_URL=mysql+pymysql://root:password@host.docker.internal:3306/autopilot \
  -e TOOLHOST_SANDBOX_DISABLED=true \
  autopilot-backend

# 查看日志
docker logs -f autopilot-backend

# 停止容器
docker stop autopilot-backend
docker rm autopilot-backend
```

---

## 常用命令速查

```bash
# ── 启动 ──
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# ── 测试 ──
pytest                                # 运行全部测试（含覆盖率报告）
pytest tests/unit/                    # 单元测试（基础设施/模型/工具）
pytest tests/services/                # 服务层测试
pytest tests/routers/                 # 路由层测试
pytest tests/integration/             # 端到端集成测试
pytest tests/unit/test_models.py      # 运行单个测试文件
pytest tests/unit/test_models.py::TestModel -k "cascade"  # 按类/关键字筛选
pytest -m "not integration"           # 跳过集成测试（快速验证）
pytest --cov=app --cov-report=html    # 生成 HTML 覆盖率报告（htmlcov/）

# ── 数据库 ──
mysql -u root -p autopilot < app/db/schema.sql   # 手动建表
mysql -u root -p -e "SHOW TABLES FROM autopilot"  # 查看表

# ── 依赖管理 ──
pip install -r requirements.txt       # 安装依赖
pip list                              # 查看已安装包
playwright install chromium           # 安装浏览器

# ── 调试 ──
curl http://localhost:8000/health     # 健康检查
curl http://localhost:8000/docs       # API 文档
curl http://localhost:8000/api/v1/projects/  # 项目列表
```

---

## 常见问题

### Q: 启动报错 "配置加载失败"？
检查 `.env` 文件是否存在且格式正确，字段名必须与 `config.py` 中的 `Settings` 类完全一致。

### Q: MySQL 连接报错 "Access denied"？
确认 `.env` 中 `DATABASE_URL` 的用户名密码正确，并已创建 `autopilot` 数据库。

### Q: 元素抓取/执行引擎报错 "NotImplementedError"？
有以下几种可能原因：
1. **使用了 `--reload` 参数**：请移除 `--reload`，IDE 沙箱会拦截 Playwright 子进程。详见上方启动说明。
2. Playwright 浏览器未安装或版本不匹配：
```bash
playwright install chromium
```

### Q: AI 代码生成返回空结果？
未配置 `OPENAI_API_KEY` 时使用 Mock 模式，返回模拟数据。如需真实生成，请配置有效的 API Key。

### Q: 调用 AI 提示 "SSL: UNEXPECTED_EOF" 或连接失败？
当前网络无法直连 `api.openai.com`。项目默认已配置 DeepSeek（`https://api.deepseek.com/v1`），国内可直连。如果你持有 OpenAI Key 且需直连，请使用支持视觉的国内中转或改用通义千问/智谱。

### Q: 如何修改 AI 模型（DeepSeek / OpenAI / 通义千问等）？
修改 `.env` 中的 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 后重启服务即可，所有 AI 调用均走 OpenAI 兼容接口。

---

## 数据库设计

### ER 关系图

```
projects (1)
  ├── page_elements (N)     FK → projects.id  CASCADE
  ├── test_cases (N)        FK → projects.id  CASCADE
  │     └── generated_codes (N)  FK → test_cases.id  CASCADE
  └── executions (N)        FK → projects.id  CASCADE
        ├── execution_steps (N)  FK → executions.id + test_cases.id  CASCADE
        │     └── heal_records (N)  FK → execution_steps.id  CASCADE
        └── execution_reports (1)  FK → executions.id UNIQUE  CASCADE
```

**级联规则**：删除项目 → 级联删除所有关联数据（元素、用例、执行、报告、自愈记录）

### 9 张表

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `projects` | 项目 | name, target_url, test_path, browser_type, headless, platform, config_json |
| `page_elements` | 页面元素 | element_type, selector, text_content, bounding_box, platform, selector_type, metadata |
| `test_cases` | 测试用例 | case_name, case_no, priority, steps(JSON), status |
| `generated_codes` | 生成代码 | code_content, is_valid, is_healed, ai_model |
| `executions` | 执行批次 | total_cases, passed_cases, failed_cases, status, platform |
| `execution_steps` | 执行步骤 | action, target_selector, screenshot_before/after, status, exception_type |
| `execution_reports` | 执行报告 | report_html, report_summary(JSON), download_url |
| `heal_records` | 自愈记录 | original_code, healed_code, retry_status, retry_count, attempts(TEXT) |

**V1.1 新增字段**：
- `projects.platform` — 项目平台（web/android），创建后只读
- `projects.config_json` — JSON 配置（Android：appium_server_url, app_package 等）
- `page_elements.platform` — 元素所属平台（web/android）
- `page_elements.selector_type` — 定位器类型（css/xpath/resource-id/content-desc 等）
- `page_elements.metadata` — 元素额外元数据（JSON）
- `execution_steps.exception_type` — 异常类型（NoSuchElementException 等）
- `heal_records.attempts` — 自愈尝试记录（JSON 数组）

完整建表语句见 [app/db/schema.sql](app/db/schema.sql)。

---

## API 接口文档

基础路径：`/api/v1`

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/projects/` | 分页项目列表（含 platform 标签） |
| `POST` | `/projects/` | 创建项目（含 platform + config_json） |
| `GET` | `/projects/{id}` | 项目详情（含完整 config_json） |
| `PUT` | `/projects/{id}` | 更新项目（platform 创建后只读） |
| `DELETE` | `/projects/{id}` | 删除项目（级联删除所有关联数据） |

**创建项目请求：**

```json
{
  "name": "示例项目",
  "target_url": "https://example.com",
  "test_path": "/",
  "browser_type": "chromium",
  "headless": true,
  "platform": "web",
  "config_json": {
    "appium_server_url": "http://localhost:4723",
    "app_package": "com.example.app",
    "app_activity": ".MainActivity",
    "device_name": "emulator-5554",
    "platform_version": "12.0",
    "automation_engine": "uiautomator2"
  }
}
```

> **platform** 可选 web / android，创建后不可修改。
> **config_json** 用于 Android 平台配置，Web 项目可省略。

### 元素抓取

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/projects/{id}/elements/crawl` | 触发元素抓取（Web 用 Playwright，Android 用 Appium） |
| `GET` | `/projects/{id}/elements/` | 元素列表（平台感知，仅返回当前项目平台的元素） |
| `DELETE` | `/projects/{id}/elements/` | 清空元素（仅当前项目平台） |

**Web 选择器生成优先级**（7 级降级策略）：

**抓取请求：**

```json
{ "max_depth": 1 }
```

**选择器生成优先级**（7 级降级策略）：

1. `data-testid` 属性
2. `id` 属性
3. `name` 属性
4. `placeholder` + 标签名
5. 稳定 class（排除动态 hash 类）
6. 文本内容 `:has-text()`
7. `nth-child` 兜底

**Android 定位器优先级**（5 级策略）：

1. `resource-id`（AppiumBy.ID）
2. `content-desc`（AppiumBy.ACCESSIBILITY_ID）
3. `text` 文本（AppiumBy.XPATH）
4. `class` + 属性（AppiumBy.XPATH）
5. XPath 兜底

**AI 感知导航（V1.2，Web 平台）**：

当 `goto` 加载失败时，自动启动 AI 感知流程，无需人工干预：

1. 以 `domcontentloaded` 重试加载（部分页面可能已部分渲染）
2. 截图当前页面状态
3. 调用 Vision API 分析截图，判断是否需要前置操作（登录、点击按钮、关闭弹窗等）
4. 解析 AI 返回的 JSON 指令，逐条执行 `click` / `fill` / `select` / `wait`
5. 重新尝试 `goto` 后继续正常抓取

> **注意**：AI 感知导航依赖**视觉能力**（Vision API）。DeepSeek 为纯文本模型不支持截图分析，此时该功能自动跳过（优雅降级为原有行为）。如需启用，请使用通义千问（qwen-vl-max）或智谱（glm-4v）等支持视觉的模型。

### 用例管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/projects/{id}/cases/import` | 上传 Excel 批量导入（multipart/form-data） |
| `GET` | `/projects/{id}/cases/` | 用例列表（?keyword=&status=&priority=&page=&size=） |
| `GET` | `/projects/{id}/cases/{case_id}` | 用例详情（含完整 steps JSON） |
| `DELETE` | `/projects/{id}/cases/{case_id}` | 删除单条用例 |
| `DELETE` | `/projects/{id}/cases/` | 批量删除 `{"ids": [1,2,3]}` |

**Excel 导入要求：**

- 格式：`.xlsx` / `.xls`
- 大小：≤ 10MB
- 支持中文/英文列名智能匹配
- 支持 3 种步骤格式：JSON 数组 / 纯文本行 / 操作+对象+数据三列
- 支持 action 类型：`navigate` / `fill` / `click` / `select` / `hover` / `assert_text` / `assert_visible` / `screenshot` / `wait`

### 代码生成

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/projects/{id}/cases/{case_id}/generate` | 单条生成 Playwright 代码 |
| `POST` | `/projects/{id}/cases/generate-batch` | 批量异步生成 `{"case_ids": [1,2,3]}` |
| `GET` | `/projects/{id}/generate-batch/{batch_id}/status` | 轮询批量生成进度 |
| `GET` | `/projects/{id}/cases/{case_id}/code` | 获取最新生成代码 |

**生成流程：**

1. 查询用例 steps + 项目 elements（平台感知，仅查询当前平台元素）
2. 智能匹配步骤 target 到页面元素 selector
3. 调用 LLM 生成代码（Web 生成 Playwright 异步代码，Android 生成 Appium 同步链式代码）
4. `ast.parse` 语法校验 + 安全黑名单检查 + 平台合约检查（Web: async def run_test(page)，Android: def run_test(driver)）
5. 存入 `generated_codes` 表，更新用例状态为 `generated`

### 执行引擎

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/projects/{id}/executions` | 创建并启动执行批次（平台感知，自动分发到对应执行引擎） |
| `GET` | `/projects/{id}/executions` | 项目执行历史列表 |
| `GET` | `/executions/{id}` | 执行详情（含步骤列表 + 截图 + 平台标签） |
| `GET` | `/executions/{id}/status` | 轮询执行进度（前端 2s 轮询） |
| `POST` | `/executions/{id}/stop` | 停止正在进行的执行 |

**创建执行请求：**

```json
{
  "case_ids": [1, 2, 3],
  "mode": "headless",
  "batch_name": "回归测试-2024Q1"
}
```

**执行状态机：**

```
pending → running → healing → completed
         ↓            ↓
        stopped     stopped
```

**平台分发机制**：
- Web 项目 → `PlaywrightService.execute()`（异步 `asyncio.run`）
- Android 项目 → `AppiumService.execute()`（同步 `Thread` 内直接调用）

**安全门禁**：未生成有效代码的用例将被拦截，拒绝执行。跨项目 case_id 将被拒绝。

### 报告

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/executions/{id}/reports/generate` | 生成 HTML 测试报告 |
| `GET` | `/executions/{id}/reports` | 获取报告信息（下载 URL） |

**报告特性：**

- 离线 HTML（内联 CSS + JS + Chart.js）
- 饼图 + 柱状图（通过/失败/耗时分布）
- 步骤截图对比（执行前 / 执行后）
- 平台标签（Web / Android）
- 异常类型分类（NoSuchElementException / StaleElementReferenceException / TimeoutException / WebDriverException）
- Lightbox 图片预览
- JSON / CSV 导出
- 打印样式优化
- 30 天自动清理

### 自愈修复

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/executions/{id}/heal` | 手动触发自愈修复 |
| `GET` | `/executions/{id}/heal-records` | 查询自愈记录 |

**自愈策略：**
1. 页面重新抓取当前元素（Web 用 Playwright，Android 用 Appium）
2. 基于失败步骤的 error context 重新生成选择器
3. LLM 分析错误上下文（Web：通用错误信息，Android：异常类型 + 截图 + 页面源码），生成修复代码
4. `ast.parse` 校验修复代码 + 平台合约检查
5. 标记 `is_healed=1`，更新 `generated_codes`
6. 自愈尝试记录保存到 `heal_records.attempts` JSON 数组

**Android 异常分类**：
- `NoSuchElementException` → ElementNotFoundError
- `StaleElementReferenceException` → StaleElementError
- `TimeoutException` → TimeoutError
- `WebDriverException` → DriverError（通用）

### 统一响应格式

```json
{
  "code": 0,
  "message": "ok",
  "data": { ... }
}
```

错误响应：

```json
{
  "code": 400,
  "message": "具体错误信息",
  "data": null
}
```

---

## 核心业务流程

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 创建项目  │ →  │ 元素抓取  │ →  │ 导入用例  │
│ 配置URL   │    │ Playwright│    │ Excel上传 │
│ 或Android │    │ 或Appium │    │ 统一格式  │
└──────────┘    └──────────┘    └──────────┘
                                      ↓
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 查看报告  │ ←  │ 执行测试  │ ←  │ 代码生成  │
│ HTML离线  │    │ 轮询进度  │    │ AI+校验   │
│ 异常分类  │    │ 平台分发  │    │ 平台感知  │
└──────────┘    └──────────┘    └──────────┘
                     ↓ 失败
               ┌──────────┐
               │ 自愈修复  │
               │ Web/Android│
               │ 异常分类  │
               └──────────┘
```

---

## 测试

测试套件采用 **4 层架构**（unit / services / routers / integration），全部运行于 SQLite 内存数据库，LLM API、Playwright、文件系统等外部依赖均通过 Mock 隔离，零外部依赖。

### 运行测试

```bash
# 运行全部测试（含覆盖率报告）
pytest

# 按层级运行
pytest tests/unit/                    # 单元测试（基础设施/模型/工具）
pytest tests/services/                # 服务层测试
pytest tests/routers/                 # 路由层测试
pytest tests/integration/             # 端到端集成测试

# 运行单个测试文件 / 用例
pytest tests/unit/test_models.py
pytest tests/services/test_service_ai.py::TestGenerateCode -k "syntax_error"

# 按标记筛选
pytest -m integration                 # 仅集成测试
pytest -m "not integration"           # 跳过集成测试（快速验证）
pytest -m unit -m service             # 多标记（单元 + 服务层）

# 覆盖率报告
pytest --cov=app --cov-report=term-missing   # 终端摘要（缺失行）
pytest --cov=app --cov-report=html           # HTML 报告（htmlcov/index.html）
```

> `pytest.ini` 已内置 `--cov=app --cov-branch --cov-report=term-missing --cov-report=html`，直接运行 `pytest` 即可输出覆盖率。

### 覆盖率现状

| 指标 | 数值 | 目标 |
|------|------|------|
| 测试用例 | 780 passed / 1 skipped | 全通过 |
| **语句覆盖率** | **≥ 90%** | ≥ 90% ✅ |
| **分支覆盖率** | **≥ 80%** | ≥ 80% ✅ |

### 各层覆盖情况

| 层级 | 目录 | 覆盖内容 |
|------|------|----------|
| 单元测试 | `tests/unit/`（14 文件） | 配置、数据库、异常、中间件、9 个 ORM 模型、Excel 解析、AST 校验/注入、Appium 注入、Android 合约、元素定位器、平台隔离、截图 |
| 服务层 | `tests/services/`（10 文件） | Project CRUD、LLM 生成、Web 执行引擎、Appium 执行引擎、元素抓取+7 级选择器、编排器+平台分发、Excel 导入、自愈+Android 分支、报告+异常分类、数据库迁移 |
| 路由层 | `tests/routers/`（8 文件） | 项目/元素/用例/生成/执行/自愈/报告全部 API 端点 |
| 集成测试 | `tests/integration/`（2 文件） | 健康检查/CORS/静态文件/生命周期 + 完整 7 步业务闭环 + 异常流水线 |

详细运行说明见 [tests/README_TEST.md](tests/README_TEST.md)。

---

## 架构设计

### 设计模式

- **路由 → 服务 → 模型** 三层架构
- **依赖注入**：`get_db()` 提供 SQLAlchemy Session
- **统一异常处理**：`AppException` → 全局异常处理器 → JSON 响应
- **请求日志中间件**：自动记录 method、path、status_code、duration、client_ip

### 安全措施

- CORS 白名单（仅允许前端域名）
- 代码安全审计（禁止 `eval` / `exec` / `__import__` / `os.system` 等）
- AST 语法校验（拦截非法代码）
- Excel 文件类型 + 大小校验
- SQLAlchemy 参数化查询（防 SQL 注入）

---

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SECRET_KEY` | `change-me-in-production` | 应用密钥（生产环境务必修改） |
| `DATABASE_URL` | `mysql+pymysql://root:password@localhost:3306/autopilot` | 数据库连接 |
| `OPENAI_API_KEY` | `""` | AI API Key（空则 Mock 模式） |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | API 地址（默认 DeepSeek，可换 OpenAI/通义千问/智谱等兼容接口） |
| `OPENAI_MODEL` | `deepseek-chat` | 默认模型（视觉能力可选 qwen-vl-max / glm-4v） |
| `PLAYWRIGHT_TIMEOUT` | `30000` | 页面加载超时（ms） |
| `PLAYWRIGHT_HEADLESS` | `true` | 无头模式 |
| `MAX_HEAL_RETRY` | `3` | 自愈最大重试次数 |
| `APPIUM_URL` | `http://localhost:4723` | Appium Server 地址 |
| `UPLOAD_DIR` | `./uploads` | 上传文件目录 |
| `REPORT_DIR` | `./reports` | HTML 报告输出目录 |
| `SCREENSHOT_DIR` | `./uploads/screenshots` | 截图存储目录 |
| `VIDEO_DIR` | `./uploads/videos` | 视频录制目录（headed 模式） |
| `EXCEL_DIR` | `./uploads/excels` | 导入 Excel 存储目录 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口 |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173","http://localhost:5174","http://127.0.0.1:5174"]` | 允许的前端域名 |
| `APP_TITLE` | `AutoPilot API` | 应用标题（Swagger 显示） |
| `API_PREFIX` | `/api/v1` | API 路径前缀 |
| `APP_VERSION` | `1.1.0` | 应用版本号 |

> **注意**：`TOOLHOST_SANDBOX_DISABLED=true` 由 `main.py` 在启动时自动设置，无需在 `.env` 中配置。