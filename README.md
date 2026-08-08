# AutoPilot

> 🤖 AI 驱动的自动化测试平台 — 让测试用例编写和执行更智能

AutoPilot 是一个完整的自动化测试平台，支持从 Excel 用例导入到 AI 代码生成、Playwright 执行、失败自愈、HTML 报告生成的全流程闭环。前端基于 Vue 3 + Element Plus，后端基于 FastAPI + Playwright + OpenAI。

---

## 项目概览

```
┌─────────────────────────────────────────────────────┐
│                    AutoPilot                         │
│                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │ 创建项目  │ → │ 元素抓取  │ → │ 导入用例  │        │
│  │ 配置URL   │   │ Playwright│   │ Excel上传 │        │
│  └──────────┘   └──────────┘   └──────────┘        │
│                                       ↓              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │ 查看报告  │ ← │ 执行测试  │ ← │ 代码生成  │        │
│  │ HTML离线  │   │ 轮询进度  │   │ AI+校验   │        │
│  └──────────┘   └──────────┘   └──────────┘        │
│                      ↓ 失败                          │
│                ┌──────────┐                          │
│                │ 自愈修复  │                          │
│                │ 重新生成  │                          │
│                └──────────┘                          │
└─────────────────────────────────────────────────────┘
```

---

## 技术架构

```
┌──────────────────────────────────────────────────┐
│                    前端 (Vue 3)                    │
│  Element Plus  │  Pinia  │  Axios  │  Highlight   │
│     localhost:5173  ← Vite 代理 →  :8000         │
└──────────────────────┬───────────────────────────┘
                       │ HTTP REST API
┌──────────────────────┴───────────────────────────┐
│                  后端 (FastAPI)                    │
│  ┌─────────┐ ┌──────────┐ ┌───────────────────┐  │
│  │ Routers │ │ Services │ │     Models        │  │
│  │ 7个路由 │ │ 8个服务  │ │ 8张表 (SQLAlchemy)│  │
│  └─────────┘ └──────────┘ └────────┬──────────┘  │
│                                     │              │
│  ┌──────────┐  ┌──────────┐  ┌─────┴──────┐      │
│  │Playwright│  │ OpenAI   │  │   MySQL    │      │
│  │ 浏览器自动化│  │ AI代码生成│  │  8.0 数据库 │      │
│  └──────────┘  └──────────┘  └────────────┘      │
└──────────────────────────────────────────────────┘
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Vue 3 + Vite | SPA 单页应用 |
| | Element Plus | UI 组件库（中文 locale） |
| | Pinia | 状态管理（4 个 Store） |
| | Axios | HTTP 客户端 |
| | Highlight.js | Python 代码高亮 |
| **后端** | FastAPI | Python 异步 Web 框架 |
| | SQLAlchemy 2.0 | ORM（MySQL / SQLite 双兼容） |
| | Pydantic 2.9 | 数据校验 |
| | Playwright 1.47 | 浏览器自动化（Chromium 无头） |
| | OpenAI API | AI 代码生成（gpt-4o / deepseek） |
| | Jinja2 | HTML 报告模板渲染 |
| **数据库** | MySQL 8.0 | 生产环境（也支持 SQLite） |
| **测试** | httpx + pytest | 35 项集成测试 |

---

## 项目结构

```
AutoPilot/
├── backend/                     # 后端服务（FastAPI）
│   ├── app/
│   │   ├── main.py              # 应用入口 + 生命周期管理
│   │   ├── config.py            # 配置加载（pydantic-settings）
│   │   ├── dependencies.py      # 依赖注入
│   │   ├── exceptions.py        # 全局异常处理
│   │   ├── schemas.py           # 通用响应模型
│   │   ├── db/                  # 数据库
│   │   │   ├── database.py      # 引擎 + Session 工厂
│   │   │   └── schema.sql       # 建表 DDL（8 张表）
│   │   ├── models/              # ORM 模型（8 张表）
│   │   ├── routers/             # API 路由（7 个模块，25 个接口）
│   │   ├── services/            # 业务逻辑层（8 个服务）
│   │   ├── utils/               # 工具模块
│   │   ├── middlewares/         # 中间件
│   │   └── prompts/             # AI Prompt 模板
│   ├── tests/                   # 测试（35 项集成测试）
│   ├── uploads/                 # 截图 + Excel 上传
│   ├── reports/                 # HTML 报告输出
│   ├── .env.example             # 环境变量模板
│   ├── Dockerfile               # Docker 构建
│   ├── requirements.txt         # Python 依赖
│   └── README.md                # 后端详细文档
├── frontend/                    # 前端应用（Vue 3）
│   ├── src/
│   │   ├── main.js              # 应用入口
│   │   ├── App.vue              # 根组件
│   │   ├── router/              # 路由配置（8 条路由）
│   │   ├── stores/              # Pinia 状态（4 个 Store）
│   │   ├── api/                 # 后端 API 封装（8 个模块）
│   │   ├── components/          # 公共组件（8 个）
│   │   ├── composables/         # 可组合函数
│   │   ├── styles/              # 全局样式
│   │   └── views/               # 页面组件（8 个页面）
│   ├── index.html               # HTML 入口
│   ├── vite.config.js           # Vite 配置 + 代理
│   ├── package.json             # 项目配置
│   └── README.md                # 前端详细文档
└── README.md                    # 本文件
```

---

## 核心功能

### 1. 项目管理
- 创建/编辑/删除项目
- 配置目标 URL、测试路径、浏览器类型
- 表单校验 + 级联删除

### 2. 元素抓取（Playwright）
- 无头浏览器自动抓取页面可交互元素
- 7 级选择器优先级降级策略（data-testid → id → name → placeholder → class → text → nth-child）
- 动态 class 过滤 + 去重
- 元素列表搜索与筛选

### 3. 用例管理（Excel 导入）
- 上传 `.xlsx` / `.xls` 批量导入
- 智能识别中文/英文列名
- 支持 3 种步骤格式（JSON 数组 / 纯文本 / 操作-对象-数据）
- 支持 9 种操作类型（navigate / fill / click / select / hover / assert_text / assert_visible / screenshot / wait）

### 4. AI 代码生成
- 智能匹配用例步骤到页面元素选择器
- 调用 LLM 生成 Playwright Python 异步代码
- `ast.parse` 语法校验 + 安全审计（禁止 eval / exec / __import__ 等）
- 单条生成 + 批量异步生成 + 进度轮询

### 5. 执行引擎
- Playwright 自动化执行（Headless / Headed）
- 安全门禁：未生成代码的用例拦截
- 每步执行前后自动截图
- 实时状态轮询 + 进度展示
- Headed 模式实时截图推送
- 支持手动停止

### 6. 自愈修复
- 失败步骤自动触发或手动触发
- 重新抓取元素 + LLM 分析错误上下文
- 生成修复代码并标记 `is_healed`
- 重试执行修复后的代码

### 7. 测试报告
- 离线 HTML 报告（内联 CSS + JS + Chart.js）
- 饼图 + 柱状图（通过率、耗时分布）
- 步骤截图对比（执行前/后）
- Lightbox 图片预览
- JSON / CSV 导出
- 打印样式优化
- 30 天自动清理

---

## 快速开始

### 前置条件

| 依赖 | 版本 |
|------|------|
| Python | 3.12+ |
| Node.js | 18+ |
| MySQL | 8.0+（可选，也支持 SQLite） |
| Playwright | Chromium 浏览器 |

### 1. 克隆项目

```bash
git clone <repo-url>
cd AutoPilot
```

### 2. 启动后端

```bash
cd backend

# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入数据库连接信息

# 创建 MySQL 数据库
mysql -u root -p -e "CREATE DATABASE autopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 启动服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

验证：`curl http://localhost:8000/health`

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

验证：浏览器打开 `http://localhost:5173`

### 4. 运行测试

```bash
cd backend
python tests/test_integration.py
```

---

## 数据库 ER 图

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

**8 张表**，级联删除链路：删项目 → 元素/用例/执行全删 → 代码/步骤/报告/自愈全删。

---

## API 接口总览

基础路径：`/api/v1`

| 模块 | 接口数 | 方法 |
|------|--------|------|
| 项目管理 | 5 | GET/POST/GET/PUT/DELETE `/projects/` |
| 元素抓取 | 3 | POST `/elements/crawl` GET/DELETE `/elements/` |
| 用例管理 | 5 | POST `/cases/import` GET/DELETE `/cases/` |
| 代码生成 | 4 | POST `/cases/{id}/generate` POST `/cases/generate-batch` GET `/code` |
| 执行引擎 | 5 | POST/GET `/executions` GET/DELETE `/executions/{id}` |
| 报告 | 2 | POST `/reports/generate` GET `/reports` |
| 自愈 | 2 | POST `/heal` GET `/heal-records` |

**总计 25 个业务接口，前后端一一对应。** 详细文档见 [backend/README.md](backend/README.md)。

---

## 页面路由

| 路径 | 页面 | 功能 |
|------|------|------|
| `/projects` | 项目列表 | 创建/编辑/删除项目 |
| `/projects/:id/elements` | 元素抓取 | Playwright 抓取 + 元素列表 |
| `/projects/:id/cases` | 用例管理 | Excel 导入 + 代码生成 |
| `/projects/:id/executions` | 执行面板 | 创建执行 + 进度轮询 |
| `/projects/:id/reports` | 报告查看 | 生成 + 预览报告 |
| `/executions/:id` | 执行详情 | 步骤时间线 + 截图对比 |
| `/reports` | 报告中心 | 所有报告列表 |

详细文档见 [frontend/README.md](frontend/README.md)。

---

## 开发指南

### 推荐 IDE

- **后端**：VS Code + Python 插件
- **前端**：VS Code + Vue Language Features (Volar)

### 开发流程

```bash
# 1. 启动后端（热重载）
cd backend && python -m uvicorn app.main:app --reload

# 2. 启动前端（热重载）
cd frontend && npm run dev

# 3. 修改代码后自动重载，无需手动重启
```

### 代码规范

- **后端**：PEP 8 + 类型注解 + 中文 docstring
- **前端**：Vue 3 Composition API + `<script setup>`
- **命名**：后端 `snake_case`，前端 `camelCase`

### 测试

```bash
# 完整集成测试（35 项）
cd backend && python tests/test_integration.py

# 单个模块测试
python tests/test_generate.py
python tests/test_execution.py
python tests/test_heal.py
python tests/test_report.py
```

---

## 配置项

### 后端（`.env`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `mysql+pymysql://root:password@localhost:3306/autopilot` | 数据库连接 |
| `OPENAI_API_KEY` | `""` | OpenAI Key（空则 Mock） |
| `OPENAI_MODEL` | `gpt-4o` | 默认模型 |
| `PLAYWRIGHT_HEADLESS` | `true` | 无头模式 |
| `PLAYWRIGHT_TIMEOUT` | `30000` | 页面超时（ms） |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 前端域名白名单 |

### 前端（`vite.config.js`）

| 代理路径 | 目标 |
|----------|------|
| `/api` | `http://127.0.0.1:8000` |
| `/reports` | `http://127.0.0.1:8000` |
| `/uploads` | `http://127.0.0.1:8000` |

---

## 完成度

| 模块 | 完成度 | 说明 |
|------|--------|------|
| 核心业务流程 | 90% | 抓取→导入→生成→执行→自愈→报告 全链路可用 |
| 前后端联调 | 100% | 35 项集成测试全部通过 |
| 接口对接 | 100% | 25 个接口全部对接前端 |
| 基础设施 | 35% | Dockerfile 已有，缺 docker-compose / CI/CD |
| 认证授权 | 0% | 未实现 |
| 并发执行 | 30% | 单线程，缺并发调度 |

---

## 详细文档

- [后端 README](backend/README.md) — 后端架构、API 文档、数据库设计、启动指南
- [前端 README](frontend/README.md) — 前端架构、路由设计、状态管理、组件说明
- [数据库 DDL](backend/app/db/schema.sql) — 完整建表语句
- [Swagger 文档](http://localhost:8000/docs) — 后端启动后访问

---

## License

MIT