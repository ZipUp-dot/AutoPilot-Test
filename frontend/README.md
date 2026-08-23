# AutoPilot Frontend

> AI 驱动的自动化测试平台 — 前端管理界面（支持 Web + Android 双平台）

基于 Vue 3 + Element Plus + Pinia 构建的 SPA 单页应用，提供项目管理、元素抓取、用例管理、AI 代码生成、执行监控、报告查看等完整前端功能，统一管理 Web 和 Android 测试项目。

---

## 技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 框架 | Vue 3 | 3.4+ |
| 构建工具 | Vite | 5.2+ |
| 路由 | Vue Router | 4.3+ |
| 状态管理 | Pinia | 2.1+ |
| UI 组件库 | Element Plus | 2.5+ |
| HTTP 客户端 | Axios | 1.7+ |
| 代码高亮 | Highlight.js | 11.9+ |
| 语言 | 中文（Element Plus 中文 locale） |

---

## 项目结构

```
frontend/
├── index.html                    # HTML 入口
├── package.json                  # 项目配置 + 依赖
├── package-lock.json             # 依赖锁定
├── vite.config.js                # Vite 构建配置 + 代理
├── Dockerfile                    # 多阶段构建（Node.js build → Nginx serve）
├── nginx.conf                    # Nginx 反向代理配置
├── .env.development              # 开发环境变量
├── public/
│   └── vite.svg                  # 静态资源
├── src/
│   ├── main.js                   # 应用入口（挂载 Vue + Pinia + Router + Element Plus）
│   ├── App.vue                   # 根组件（布局壳：侧边栏 + 顶部栏 + 内容区）
│   ├── router/
│   │   └── index.js              # 路由配置（10 条路由）
│   ├── stores/                   # Pinia 状态管理
│   │   ├── index.js              # 统一导出
│   │   ├── projectStore.js       # 项目状态（含 platform）
│   │   ├── caseStore.js          # 用例状态 + 批量生成轮询
│   │   ├── executionStore.js     # 执行状态 + 进度轮询
│   │   └── reportStore.js        # 报告状态
│   ├── api/                      # 后端 API 封装（8 个模块）
│   │   ├── index.js              # Axios 实例 + 拦截器
│   │   ├── project.js            # 项目 CRUD（含 platform / config_json）
│   │   ├── element.js            # 元素抓取
│   │   ├── case.js               # 用例管理
│   │   ├── generate.js           # 代码生成 + 批量进度
│   │   ├── execution.js          # 执行管理
│   │   ├── report.js             # 报告生成
│   │   └── heal.js               # 自愈修复
│   ├── components/               # 公共组件
│   │   ├── AppHeader.vue         # 顶部导航栏
│   │   ├── AppSidebar.vue        # 侧边栏菜单
│   │   ├── CaseStatusTag.vue     # 用例状态标签
│   │   ├── CodePreview.vue       # 代码预览（Highlight.js）
│   │   ├── EmptyState.vue        # 空状态占位
│   │   ├── ExecutionStatusTag.vue # 执行状态标签
│   │   ├── LoadingMask.vue       # 加载遮罩
│   │   └── PriorityTag.vue       # 优先级标签
│   ├── composables/              # 可组合函数
│   │   ├── usePolling.js         # 通用轮询 Hook（执行进度/批量生成状态）
│   │   └── useWebSocket.js       # WebSocket 连接（预留）
│   ├── styles/                   # 全局样式
│   │   ├── variables.css         # CSS 变量
│   │   └── global.css            # 全局样式重置
│   └── views/                    # 页面组件
│       ├── Dashboard.vue         # 仪表盘（含平台标签）
│       ├── ProjectList.vue       # 项目列表（含 platform 选择/展示 + Android 配置）
│       ├── ProjectDetail.vue     # 项目详情（含 platform 标签 + Android 配置编辑）
│       ├── ExecutionDetail.vue   # 执行详情（含平台标签 + exception_type 展示）
│       ├── ReportCenter.vue      # 报告中心（含平台标签）
│       └── project/              # 项目子页面
│           ├── ElementCapture.vue # 元素抓取页
│           ├── CaseManagement.vue # 用例管理页
│           ├── ExecutionPanel.vue  # 执行面板（含平台标签）
│           └── ReportViewer.vue    # 报告查看页
└── dist/                         # 构建产物
```

---

## 快速开始

### 1. 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Node.js | 18+ | 推荐 20 LTS |
| npm | 9+ | 或 pnpm / yarn |

### 2. 安装依赖

```bash
# 进入前端目录
cd frontend

# 安装依赖
npm install
```

### 3. 配置环境变量

`.env.development`（已内置，无需修改）：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
VITE_APP_TITLE=AutoPilot Test Platform
```

### 4. 启动开发服务器

```bash
# 启动（默认 http://localhost:5173）
npm run dev

# 指定端口
npm run dev -- --port 3000
```

启动成功后终端显示：

```
VITE v5.x.x  ready in xxx ms
➜  Local:   http://127.0.0.1:5173/
➜  Network: use --host to expose
```

### 5. 构建生产版本

```bash
# 构建到 dist/ 目录
npm run build

# 预览构建产物
npm run preview
```

### 6. Docker 部署

推荐使用项目根目录的 `docker-compose.yml` 一键启动全部服务：

```bash
# 在项目根目录执行
cd ..
docker compose up -d
```

访问 `http://localhost:8080`。

前端 Dockerfile 采用**多阶段构建**：
- **Stage 1**（`node:20-alpine`）：`npm ci` → `npm run build` 编译 Vue 产物
- **Stage 2**（`nginx:alpine`）：托管静态文件 + 反向代理后端 API

Nginx 配置（`frontend/nginx.conf`）负责：
- 静态文件服务 + SPA 路由回退
- `/api/*` → 反向代理到 `backend:8000`
- `/reports/*`、`/uploads/*` → 反向代理到后端
- Gzip 压缩 + 安全头

### 7. 代理配置

Vite 开发服务器已配置代理（`vite.config.js`），无需前端额外配置：

```js
// vite.config.js
server: {
  port: 5173,
  host: '127.0.0.1',
  proxy: {
    '/api':     { target: 'http://127.0.0.1:8000', changeOrigin: true },
    '/reports': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    '/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true },
  },
}
```

| 前端请求路径 | 代理到 |
|-------------|--------|
| `/api/*` | `http://127.0.0.1:8000` |
| `/reports/*` | `http://127.0.0.1:8000` |
| `/uploads/*` | `http://127.0.0.1:8000` |

> **前提**：后端服务已启动在 `http://127.0.0.1:8000`。如果后端端口不同，修改 `vite.config.js` 中 `proxy` 的 `target` 值。

---

## 页面路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/` | → 重定向到 `/projects` | 默认首页 |
| `/projects` | ProjectList | 项目列表，支持创建/编辑/删除（含 platform 选择 + Android 配置） |
| `/projects/:id` | ProjectDetail | 项目详情（Tab 容器，含 platform 标签 + Android 配置编辑） |
| `/projects/:id/elements` | ElementCapture | 元素抓取 + 列表 |
| `/projects/:id/cases` | CaseManagement | 用例管理 + Excel 导入 + 代码生成 |
| `/projects/:id/executions` | ExecutionPanel | 执行历史 + 创建执行（含平台标签） |
| `/projects/:id/reports` | ReportViewer | 报告查看 |
| `/executions/:executionId` | ExecutionDetail | 执行详情 + 步骤截图对比 + exception_type 展示 |
| `/reports` | ReportCenter | 报告中心（含平台标签） |
| `/dashboard` | Dashboard | 仪表盘（含平台标签 + 最近执行） |
| `/*` | → 重定向到 `/projects` | 404 兜底 |

---

## 状态管理

4 个 Pinia Store，覆盖全部业务模块：

| Store | 文件 | 职责 |
|-------|------|------|
| `useProjectStore` | [projectStore.js](src/stores/projectStore.js) | 项目 CRUD、列表缓存 |
| `useCaseStore` | [caseStore.js](src/stores/caseStore.js) | 用例 CRUD、Excel 导入、代码生成、**批量生成进度轮询** |
| `useExecutionStore` | [executionStore.js](src/stores/executionStore.js) | 执行创建、状态轮询、截图获取 |
| `useReportStore` | [reportStore.js](src/stores/reportStore.js) | 报告生成、缓存管理 |

---

## API 封装

8 个 API 模块，通过 Axios 实例统一管理：

| 模块 | 文件 | 接口数 |
|------|------|--------|
| 项目 | [project.js](src/api/project.js) | 5 |
| 元素 | [element.js](src/api/element.js) | 3 |
| 用例 | [case.js](src/api/case.js) | 5 |
| 代码生成 | [generate.js](src/api/generate.js) | 4 |
| 执行 | [execution.js](src/api/execution.js) | 5 |
| 报告 | [report.js](src/api/report.js) | 2 |
| 自愈 | [heal.js](src/api/heal.js) | 2 |

**总计 26 个前端 API 调用，与后端 26 个业务接口一一对应。**

### Axios 实例配置

- 基础路径：`/api/v1`
- 超时：30 秒
- 响应拦截器：自动提取 `data` 字段，统一处理 `code !== 0` 错误

---

## 公共组件

| 组件 | 说明 |
|------|------|
| **AppHeader** | 顶部导航栏，显示当前页面标题 + 面包屑 |
| **AppSidebar** | 侧边栏菜单，根据路由自动高亮 |
| **CaseStatusTag** | 用例状态标签（`pending` / `generated` / `executed` / `failed`） |
| **CodePreview** | 代码预览面板，集成 Highlight.js 语法高亮（Python） |
| **EmptyState** | 空状态占位图，用于列表为空时的友好提示 |
| **ExecutionStatusTag** | 执行状态标签（`running` / `completed` / `failed` / `stopped` / `healing`） |
| **LoadingMask** | 全屏加载遮罩，带 loading 动画 |
| **PriorityTag** | 优先级标签（`P0` / `P1` / `P2` / `P3`） |

---

## 核心功能页面

### 1. 项目列表（ProjectList）

- 分页卡片列表展示所有项目
- 创建项目：填写名称、URL、测试路径、浏览器类型、**platform 选择（web/android）**、**Android 配置（Appium 地址、package、activity、设备名等）**
- 编辑项目：修改项目配置，**platform 创建后只读**，**Android 配置动态编辑**
- 删除项目：级联删除所有关联数据（需二次确认）
- 表单校验：名称必填，URL 格式校验
- **平台列**：Web 蓝色标签，Android 绿色标签

### 2. 项目详情（ProjectDetail）

Tab 容器，包含 4 个子页面，**顶部显示 platform 标签**：

| Tab | 组件 | 功能 |
|-----|------|------|
| 页面元素 | ElementCapture | 触发 Playwright/Appium 抓取 → 元素列表 → 搜索筛选 |
| 测试用例 | CaseManagement | Excel 导入（含**导入结果分档反馈**：全部成功/部分成功/全部失败+错误明细） → 用例列表 → 单条/批量生成代码 → 进度条 |
| 执行历史 | ExecutionPanel | 创建执行 → 执行列表（含平台标签）→ 状态轮询 → 停止执行 |
| 测试报告 | ReportViewer | 生成报告 → 内嵌 iframe 预览 → 下载 |

**Android 配置编辑**：项目详情页显示"Android 配置"按钮，弹窗编辑 Appium 配置 6 个字段。

### 3. 执行详情（ExecutionDetail）

- 步骤时间线展示（含执行前/后截图对比）
- 实时进度轮询（2 秒间隔）
- **平台标签**（Web / Android）
- **exception_type 标签**：显示具体异常类型（NoSuchElementException 等）
- Headed 模式实时截图推送
- 截图 Lightbox 弹窗放大查看
- 错误信息高亮显示
- 自愈修复触发入口

### 4. 仪表盘（Dashboard）

- 项目总数、用例总数、执行总数统计卡片
- 最近执行列表（含**平台标签**，快速跳转）
- 执行记录表格含"平台"列

### 5. 报告中心（ReportCenter）

- 所有已生成报告列表（含**平台标签**）
- 按项目筛选
- 空状态引导（无报告时提示创建）

---

## 常用命令速查

```bash
# ── 启动 ──
cd frontend
npm install          # 安装依赖
npm run dev          # 启动开发服务器（http://localhost:5173）
npm run dev -- --port 3000  # 指定端口

# ── 构建 ──
npm run build        # 生产构建 → dist/
npm run preview      # 预览构建产物

# ── 依赖管理 ──
npm install                # 安装所有依赖
npm install <package>      # 安装单个包
npm update                 # 更新依赖
npm ls                     # 查看依赖树

# ── 清理 ──
# 删除 node_modules 和锁文件重新安装
rm -rf node_modules package-lock.json
npm install
```

---

## 前后端联调

### 启动顺序

```bash
# 1. 先启动后端（注意：不要加 --reload 参数）
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. 再启动前端
cd frontend
npm run dev
```

> ⚠️ **重要**：后端启动时**禁止使用 `--reload` 参数**，否则 IDE 沙箱会拦截 Playwright 子进程，导致元素抓取和执行引擎报错 `NotImplementedError`。

### 访问地址

| 服务 | 地址 |
|------|------|
| 前端页面 | http://localhost:5173 |
| 后端 API | http://localhost:8000/api/v1 |
| Swagger 文档 | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/health |

### 代理原理

```
浏览器 → http://localhost:5173/api/v1/projects/
         ↓ Vite 代理
         → http://127.0.0.1:8000/api/v1/projects/
```

前端无需配置 CORS，所有 `/api` 请求由 Vite 开发服务器透明转发到后端。

---

## 与后端接口对应关系

| 前端 `src/api/` | 调用方法 | 后端端点 |
|-----------------|---------|----------|
| `project.js` | `list / create / detail / update / delete` | `GET/POST/GET/PUT/DELETE /projects/` |
| `element.js` | `crawl / list / clear` | `POST /elements/crawl` `GET-DELETE /elements/` |
| `case.js` | `importExcel / list / detail / delete / deleteBatch` | `POST /cases/import` `GET/DELETE /cases/` |
| `generate.js` | `generateCase / generateBatch / getBatchStatus / getLatestCode` | `POST /cases/{id}/generate` `POST /cases/generate-batch` `GET /generate-batch/{id}/status` `GET /cases/{id}/code` |
| `execution.js` | `create / list / detail / status / stop` | `POST /executions` `GET /executions` `GET /executions/{id}` `GET /executions/{id}/status` `POST /executions/{id}/stop` |
| `report.js` | `generate / getInfo` | `POST /reports/generate` `GET /reports` |
| `heal.js` | `triggerHeal / getHealRecords` | `POST /heal` `GET /heal-records` |

---

## 常见问题

### Q: 启动报错 "Cannot find package 'vite'"？
依赖未安装，执行 `npm install`。

### Q: 页面空白，控制台报 CORS 错误？
检查后端是否启动在 `http://127.0.0.1:8000`，Vite 代理依赖后端运行。后端已配置 CORS 白名单支持 `localhost:5173` / `127.0.0.1:5173` / `localhost:5174` / `127.0.0.1:5174`。

### Q: 元素抓取或执行测试报错？
确认后端启动时**没有使用 `--reload` 参数**。IDE 沙箱会拦截 Playwright 子进程调用，详见后端 README 的启动说明。

### Q: API 请求返回 404？
确认后端路由前缀为 `/api/v1`，前端 Axios 基础路径已配置为 `/api/v1`。

### Q: 批量生成进度条不显示？
检查后端 `GET /generate-batch/{batch_id}/status` 接口是否正常（`batch_id` 存在且未过期）。

### Q: npm install 慢？
设置国内镜像：
```bash
npm config set registry https://registry.npmmirror.com
```

### Q: 如何更换后端地址？
修改 `vite.config.js` 中 `proxy` 的 `target` 值，然后重启开发服务器。