# AutoPilot Backend

> AI 驱动的自动化测试平台 — 后端服务

基于 FastAPI + SQLAlchemy + Playwright 构建，提供项目管理、元素抓取、Excel 用例导入、AI 代码生成、Playwright 执行、自愈修复、HTML 报告生成等完整测试能力。

---

## 技术栈

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 |
| 数据库 | MySQL 8.0 / SQLite（双兼容） |
| 浏览器自动化 | Playwright 1.47（Chromium 无头模式） |
| AI 代码生成 | OpenAI API（gpt-4o / deepseek-chat，支持 Mock 模式） |
| 数据校验 | Pydantic 2.9 |
| 异步 | asyncio + threading |
| Excel 解析 | openpyxl 3.1 |
| 报告模板 | Jinja2 3.1 |
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
│   ├── schemas.py               # Pydantic 响应模型
│   ├── db/
│   │   ├── database.py          # SQLAlchemy 引擎 + Session 工厂
│   │   └── schema.sql           # 数据库建表脚本
│   ├── models/                  # ORM 模型
│   │   ├── project.py           # 项目表
│   │   ├── element.py           # 页面元素表
│   │   ├── test_case.py         # 测试用例表
│   │   ├── test_step.py         # 测试步骤（已合并到 test_case 的 JSON 字段）
│   │   ├── generated_code.py    # 生成代码表
│   │   ├── execution.py         # 执行批次表
│   │   ├── execution_step.py    # 执行步骤表
│   │   ├── report.py            # 执行报告表
│   │   └── heal_record.py       # 自愈记录表
│   ├── routers/                 # API 路由
│   │   ├── projects.py          # 项目 CRUD
│   │   ├── elements.py          # 元素抓取 / 列表 / 清空
│   │   ├── cases.py             # 用例导入 / 列表 / 删除
│   │   ├── generate.py          # 代码生成（单条 + 批量）
│   │   ├── executions.py        # 执行管理（创建 / 轮询 / 停止）
│   │   ├── reports.py           # 报告生成 / 查询
│   │   └── heal.py              # 自愈修复
│   ├── services/                # 业务逻辑层
│   │   ├── project_service.py   # 项目 CRUD
│   │   ├── element_service.py   # 元素抓取 + 7 级选择器生成
│   │   ├── case_service.py      # Excel 解析 + 用例管理
│   │   ├── ai_service.py        # LLM 调用 + 代码生成
│   │   ├── playwright_service.py # Playwright 执行引擎
│   │   ├── orchestrator.py      # 执行编排器（生成→执行→监听→报告）
│   │   ├── heal_service.py      # 自愈修复
│   │   └── report_service.py    # HTML 报告生成
│   ├── utils/                   # 工具模块
│   │   ├── excel_parser.py      # Excel 智能解析（中文列名）
│   │   ├── code_validator.py    # AST 语法校验 + 安全审计
│   │   ├── code_injector.py     # 截图/日志注入
│   │   └── screenshot.py        # 截图工具类
│   ├── prompts/                 # AI Prompt 模板
│   └── middlewares/             # 中间件
│       ├── logging.py           # 请求日志
│       └── timing.py            # 响应时间
├── tests/                       # 测试
│   ├── test_integration.py      # 前后端联调测试（35 项）
│   ├── test_cases.py            # 用例管理测试
│   ├── test_generate.py         # 代码生成测试
│   ├── test_execution.py        # 执行引擎测试
│   ├── test_heal.py             # 自愈测试
│   ├── test_report.py           # 报告测试
│   ├── test_security.py         # 安全测试
│   └── ...
├── data/                        # 数据目录（SQLite 模式）
├── uploads/                     # 上传文件
│   ├── screenshots/             # 执行截图
│   ├── excels/                  # 导入的 Excel 文件
│   └── videos/                  # 视频录制（预留）
├── reports/                     # 生成的 HTML 报告
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
| MySQL | 8.0+ | 可选，也可用 SQLite 免安装 |
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

**方式一：MySQL**

```bash
# 登录 MySQL
mysql -u root -p

# 创建数据库
CREATE DATABASE autopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 退出
exit
```

**方式二：SQLite（免安装）**

无需任何操作，首次启动会自动创建 `data/autopilot.db`。

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
# ── 数据库（二选一）──
# MySQL 模式
DATABASE_URL=mysql+pymysql://root:你的密码@localhost:3306/autopilot

# SQLite 模式（注释掉上面那行，用这行）
# DATABASE_URL=sqlite:///./data/autopilot.db

# ── AI 代码生成（可选）──
# 不填则使用 Mock 模式，不影响其他功能
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# ── Playwright ──
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT=30000
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

#### 开发模式（带热重载）

```bash
# 在 backend 目录下执行
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

参数说明：

| 参数 | 说明 |
|------|------|
| `app.main:app` | 模块路径：app/main.py 中的 app 实例 |
| `--host 127.0.0.1` | 监听地址（仅本机访问） |
| `--host 0.0.0.0` | 监听所有网卡（允许局域网访问） |
| `--port 8000` | 监听端口 |
| `--reload` | 代码变更自动重启（开发模式） |
| `--workers 4` | 多进程模式（生产环境，不与 --reload 共用） |

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

```bash
# 构建镜像
docker build -t autopilot-backend .

# 运行容器
docker run -d \
  --name autopilot-backend \
  -p 8000:8000 \
  --env-file .env \
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
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# ── 测试 ──
python tests/test_integration.py      # 完整联调测试（35 项）
python tests/test_generate.py         # 代码生成测试
python tests/test_execution.py        # 执行引擎测试
python tests/test_heal.py             # 自愈测试
python tests/test_report.py           # 报告测试

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
Playwright 浏览器未安装或版本不匹配：
```bash
playwright install chromium
```

### Q: AI 代码生成返回空结果？
未配置 `OPENAI_API_KEY` 时使用 Mock 模式，返回模拟数据。如需真实生成，请配置有效的 API Key。

### Q: 如何切换数据库（MySQL ↔ SQLite）？
修改 `.env` 中的 `DATABASE_URL` 后重启服务即可。SQLAlchemy ORM 层自动适配，无需改代码。

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

### 8 张表

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `projects` | 项目 | name, target_url, test_path, browser_type, headless |
| `page_elements` | 页面元素 | element_type, selector, text_content, bounding_box |
| `test_cases` | 测试用例 | case_name, case_no, priority, steps(JSON), status |
| `generated_codes` | 生成代码 | code_content, is_valid, is_healed, ai_model |
| `executions` | 执行批次 | total_cases, passed_cases, failed_cases, status |
| `execution_steps` | 执行步骤 | action, target_selector, screenshot_before/after, status |
| `execution_reports` | 执行报告 | report_html, report_summary(JSON), download_url |
| `heal_records` | 自愈记录 | original_code, healed_code, retry_status, retry_count |

完整建表语句见 [app/db/schema.sql](app/db/schema.sql)。

---

## API 接口文档

基础路径：`/api/v1`

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/projects/` | 分页项目列表（?page=1&size=20） |
| `POST` | `/projects/` | 创建项目 |
| `GET` | `/projects/{id}` | 项目详情 |
| `PUT` | `/projects/{id}` | 更新项目 |
| `DELETE` | `/projects/{id}` | 删除项目（级联删除所有关联数据） |

**创建项目示例：**

```json
{
  "name": "示例项目",
  "target_url": "https://example.com",
  "test_path": "/",
  "browser_type": "chromium",
  "headless": true
}
```

### 元素抓取

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/projects/{id}/elements/crawl` | 触发 Playwright 元素抓取 |
| `GET` | `/projects/{id}/elements/` | 元素列表（?keyword=&element_type=&page=&size=） |
| `DELETE` | `/projects/{id}/elements/` | 清空所有元素 |

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

1. 查询用例 steps + 项目 elements
2. 智能匹配步骤 target 到页面元素 selector
3. 调用 LLM 生成 Playwright Python 异步代码
4. `ast.parse` 语法校验 + 安全黑名单检查
5. 存入 `generated_codes` 表，更新用例状态为 `generated`

### 执行引擎

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/projects/{id}/executions` | 创建并启动执行批次 |
| `GET` | `/projects/{id}/executions` | 项目执行历史列表 |
| `GET` | `/executions/{id}` | 执行详情（含步骤列表 + 截图） |
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

**安全门禁**：未生成有效代码的用例将被拦截，拒绝执行。

### 报告

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/executions/{id}/reports/generate` | 生成 HTML 测试报告 |
| `GET` | `/executions/{id}/reports` | 获取报告信息（下载 URL） |

**报告特性：**

- 离线 HTML（内联 CSS + JS + Chart.js）
- 饼图 + 柱状图（通过/失败/耗时分布）
- 步骤截图对比（执行前 / 执行后）
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
1. 页面重新抓取当前元素
2. 基于失败步骤的 error context 重新生成选择器
3. LLM 分析错误上下文，生成修复代码
4. `ast.parse` 校验修复代码
5. 标记 `is_healed=1`，更新 `generated_codes`

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
└──────────┘    └──────────┘    └──────────┘
                                      ↓
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 查看报告  │ ←  │ 执行测试  │ ←  │ 代码生成  │
│ HTML离线  │    │ 轮询进度  │    │ AI+校验   │
└──────────┘    └──────────┘    └──────────┘
                     ↓ 失败
               ┌──────────┐
               │ 自愈修复  │
               │ 重新生成  │
               └──────────┘
```

---

## 测试

```bash
# 运行完整联调测试（35 项）
python tests/test_integration.py

# 运行单个模块测试
python tests/test_generate.py
python tests/test_execution.py
python tests/test_heal.py
python tests/test_report.py
```

**测试覆盖：**

| 模块 | 测试数 | 覆盖内容 |
|------|--------|----------|
| 项目 CRUD | 6 | 创建/列表/详情/编辑/校验/删除 |
| 元素抓取 | 3 | 抓取/列表/搜索 |
| 用例导入 | 4 | 导入/列表/搜索/详情 |
| 代码生成 | 3 | 单条/批量/获取代码 |
| 执行引擎 | 6 | 门禁/创建/轮询/详情/截图/列表 |
| 报告 | 10 | 生成/HTML/图表/导出/打印/缓存 |
| 自愈 | 1 | 记录列表 |
| 停止/清理 | 2 | 停止/删除 |

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
| `DATABASE_URL` | `mysql+pymysql://root:password@localhost:3306/autopilot` | 数据库连接 |
| `OPENAI_API_KEY` | `""` | OpenAI API Key（空则 Mock） |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 代理地址 |
| `OPENAI_MODEL` | `gpt-4o` | 默认模型 |
| `PLAYWRIGHT_TIMEOUT` | `30000` | 页面加载超时（ms） |
| `PLAYWRIGHT_HEADLESS` | `true` | 无头模式 |
| `MAX_HEAL_RETRY` | `3` | 自愈最大重试次数 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 允许的前端域名 |
| `APP_TITLE` | `AutoPilot API` | 应用标题 |
| `API_PREFIX` | `/api/v1` | API 路径前缀 |