# 🤖 AutoPilot — AI 驱动的智能 Web UI 自动化测试平台

> **"让测试回归业务，让 AI 搞定代码。"**
>
> 一个面向测试工程师与开发团队的轻量级、开箱即用的 AI 自动化测试助手。
> 核心理念：**先感知页面，再生成用例**。

> 🎯 **不是又一个 AI 测试框架，而是一个 Excel → 可执行脚本的转化器。**


## 一、项目定位

AutoPilot 是一个**环境感知型** AI Web UI 自动化测试平台。

传统 AI 生成脚本属于"盲猜式"——AI 不了解页面真实 DOM 结构，导致元素定位频繁失败。AutoPilot 在执行前先用 Playwright 抓取目标页面的**真实可交互元素**，将元素上下文与测试用例一并喂给 LLM，从而大幅提升首次生成准确率。

**一句话总结**：给 AI 装上"眼睛"，让它基于真实环境写代码，而不是凭空猜测。


## 二、市场定位：跟别人有什么不同？

| 对比维度 | browser-use | Playwright MCP | Bugninja AI | **AutoPilot** |
| :--- | :--- | :--- | :--- | :--- |
| **使用方式** | 写 Python 代码 | 写 Python 代码 | Web 界面（商业 SaaS） | **Web 界面（开源免费）** |
| **面向用户** | 开发者 | 开发者 | QA 团队 | **测试人员（含手工测试）** |
| **输入** | 自然语言指令 | 自然语言指令 | Excel/Word | **Excel 用例** |
| **输出** | AI 实时执行 | AI 实时执行 | 平台内部脚本 | **标准 .py 文件，可下载带走** |
| **开源** | ✅ | ✅ | ❌ | ✅ |
| **中文场景优化** | 通用 | 通用 | 部分支持 | **✅ Excel 列名智能匹配** |

**差异化壁垒：**

1. **形态壁垒**：Web 平台 vs 命令行库——测试人员不需要懂 Python
2. **场景壁垒**：Excel 用例转化 vs 自然语言执行——贴合国内测试团队实际工作流
3. **输出壁垒**：生成标准 `.py` 文件——用户不被平台锁定，可自由接入 CI/CD


## 三、解决什么问题

| 痛点 | 传统方案 | AutoPilot 方案 |
| :--- | :--- | :--- |
| **编写门槛高** | 要求测试人员具备编程能力 | 零代码，Excel 导入即可生成脚本 |
| **维护成本大** | 前端迭代导致定位器失效，维护占 60%+ 工时 | 智能元素抓取 + 执行容错重试，降低维护开销 |
| **用例转化难** | Excel 用例需人工逐条翻译为代码 | 批量导入，AI 自动转化为 Playwright 代码 |
| **AI 生成不稳定** | 纯自然语言生成，缺乏页面结构上下文 | **环境感知 → 精准生成**，准确率提升显著 |


## 四、核心业务闭环

AutoPilot 构建了完整的 7 步自动化工作流：

```
环境配置 → 元素抓取 → 用例导入 → AI 精准生成 → 可视化执行 → [失败时容错重试] → 报告输出
```

| 步骤 | 说明 |
| :--- | :--- |
| **1. 环境配置** | Web 端输入目标 URL 与测试路径，一键创建项目 |
| **2. 智能元素抓取** | Playwright 自动遍历页面，提取所有可交互元素（按钮、输入框、链接等），结构化返回前端列表 |
| **3. 用例导入** | 上传标准 Excel 测试用例，系统自动识别中英文列名，批量解析 |
| **4. AI 精准生成** | 将**真实元素列表** + **用例步骤**作为上下文，调用 LLM 生成 Playwright 异步代码，并做基础语法校验 |
| **5. 可视化执行与监控** | 支持有头/无头模式运行，Web 界面实时展示执行进度、每步截图与完整日志 |
| **6. 执行容错重试（V1.0）** | 执行失败时自动截图、捕获错误日志，采用固定策略重试（超时加倍 + 刷新元素定位，最多 3 次） |
| **7. 报告生成** | 自动生成离线 HTML 可视化报告，含通过率、失败详情、截图对比、日志追溯 |


## 五、产品能力

| 能力 | 说明 |
| :--- | :--- |
| **元素定位准确率** | 基于真实 DOM 上下文生成定位器，设计值 ≥ 70% |
| **单条用例端到端耗时** | 标准用例、网络正常情况下 ≤ 60 秒 |
| **Excel 批量导入** | 单次支持 100 行以上用例无丢失 |
| **开源协议** | MIT |


## 六、技术栈

| 端 | 技术 |
| :--- | :--- |
| **后端** | FastAPI 0.115 · SQLAlchemy 2.0 · Pydantic 2.9 · Playwright 1.47 · httpx 0.27 · openpyxl 3.1 · Jinja2 3.1 · Python 3.12+ |
| **前端** | Vue 3 · Vite 5 · Vue Router 4 · Pinia 2 · Element Plus 2.5 · Axios 1.7 · Highlight.js 11.9 |
| **数据库** | MySQL 8.0（生产）/ SQLite（开发与测试，零外部依赖） |
| **测试** | pytest 7.4+ · pytest-asyncio · pytest-cov · pytest-mock · factory-boy · faker · freezegun |


## 七、项目结构

```
AutoPilot/
├── backend/                        # 后端服务（FastAPI）
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口 + 生命周期
│   │   ├── config.py               # 配置加载（pydantic-settings 单例）
│   │   ├── dependencies.py         # 依赖注入（get_db 等）
│   │   ├── exceptions.py           # 全局异常处理器
│   │   ├── schemas.py              # Pydantic 响应模型
│   │   ├── db/                     # SQLAlchemy 引擎 + schema.sql
│   │   ├── models/                 # 8 个 ORM 模型（级联删除）
│   │   ├── routers/                # 7 个 API 路由模块
│   │   ├── services/               # 8 个业务服务（含编排器）
│   │   ├── utils/                  # Excel 解析 / AST 校验 / 注入 / 截图
│   │   ├── prompts/                # AI Prompt 模板（支持热更新）
│   │   ├── templates/              # HTML 报告模板
│   │   └── middlewares/            # 请求日志 + 响应计时
│   ├── tests/                      # pytest 四层测试套件
│   │   ├── conftest.py             # 共享 Fixture（SQLite 内存库 + 全 Mock）
│   │   ├── factories.py            # 8 个 factory-boy 工厂类
│   │   ├── unit/                   # 单元测试（10 文件）
│   │   ├── services/               # 服务层测试（8 文件）
│   │   ├── routers/                # 路由层测试（8 文件）
│   │   └── integration/            # 端到端集成测试（2 文件）
│   ├── data/                       # SQLite 数据库文件
│   ├── uploads/                    # 截图 / Excel / 视频
│   ├── reports/                    # HTML 报告（30 天自动清理）
│   ├── pytest.ini                  # pytest 配置（--cov-branch）
│   └── requirements.txt
└── frontend/                       # 前端管理界面（Vue 3）
    ├── src/
    │   ├── api/                    # 7 个 API 模块（25 个接口）
    │   ├── components/             # 8 个公共组件
    │   ├── composables/            # 轮询 / WebSocket
    │   ├── stores/                 # 4 个 Pinia Store
    │   ├── router/                 # 路由配置
    │   ├── styles/                 # 全局样式
    │   └── views/                  # 9 个页面视图
    ├── Dockerfile                  # 多阶段构建（Node → Nginx）
    └── nginx.conf                  # 反向代理 + SPA 回退
```


## 八、项目价值

### 对个人开发者
- **面试核心竞争力**：展示全栈开发 + AI 工程化落地 + 测试工具产品设计的复合能力
- **开源作品集**：一个完整的、可运行的开源项目，是面试时最有力的技术信任背书
- **技术深度**：实践 Prompt Engineering、Playwright 自动化、异步 FastAPI、现代前端工程化
- **开源影响力**：为国内 "AI + 测试" 社区贡献可落地的轻量级方案

### 对团队与企业
- **降低门槛**：手工测试人员无需编程即可参与 UI 自动化
- **提升效率**：用例转化从"人工编写"变为"AI 精准生成"
- **减少维护**：元素抓取提升定位器稳定性，容错重试覆盖暂时性加载异常，降低脚本维护成本


## 九、迭代路线图

| 版本 | 状态 | 核心内容 |
| :--- | :--- | :--- |
| **V1.0** | ✅ MVP 已发布 | 跑通"抓取 → 导入 → 生成 → 执行 → 容错重试 → 报告"全链路 |
| **V1.1** | 📅 规划中 | AI 自愈升级（LLM 分析错误 + 重写定位器）、更丰富的测试对比报告 |
| **V2.0** | 📅 规划中 | Web 端定时任务（CI/CD 集成）、用户权限管理、APP UI 自动化支持（扩展 Appium） |


## 十、快速开始

### 效果演示

<p align="center">
  <img src="demo/MVP_DEMO.gif" width="800" alt="AutoPilot 演示">
</p>

### 前置条件

- Docker 20.10+（已内置 Docker Compose）

### 一键启动

```bash
# 克隆仓库
git clone https://gitee.com/Mr-6Lawrence/auto-pilot-test.git
cd auto-pilot-test

# 创建环境文件，填入你的 AI API Key
# （Windows 用户请手动创建 .env 文件）
cat > .env << 'EOF'
OPENAI_API_KEY=sk-your-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
EOF

docker compose up -d
```

首次构建约 5~8 分钟，完成后访问 `http://localhost:8080` 即可使用。

### 本地开发

```bash
# 后端（注意：禁止 --reload，避免沙箱拦截 Playwright）
cd backend
pip install -r requirements.txt
playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm install
npm run dev   # http://localhost:5173
```

### 运行测试

```bash
cd backend
pytest                          # 运行全部测试（含覆盖率）
pytest tests/unit/              # 仅单元测试
pytest tests/services/          # 仅服务层测试
pytest tests/routers/           # 仅路由层测试
pytest tests/integration/       # 仅端到端集成测试
```

测试套件采用**四层架构**（unit / services / routers / integration），全部运行于 SQLite 内存数据库、零外部依赖：
- LLM API、Playwright、文件系统均通过 Mock 隔离
- 当前语句覆盖率 **≥ 90%**、分支覆盖率 **≥ 80%**（pytest.ini 已配置 `--cov-branch`）
- 完整说明见 [tests/README_TEST.md](backend/tests/README_TEST.md)

> 详细技术文档、API 接口、数据库设计请参阅：
> - [📁 后端文档](backend/README.md)
> - [📁 前端文档](frontend/README.md)


## 十一、贡献指南

欢迎 Star、Fork 与贡献代码！无论是 Prompt 优化、自愈策略改进，还是新功能模块，都期待你的参与。

如有问题，请提交 Issue 或联系维护者。


## 📄 开源许可

本项目基于 **MIT 许可证** 开源。


## 📌 版本信息

| 项目 | 内容 |
| :--- | :--- |
| **当前版本** | V1.0 MVP |
| **文档版本** | V2.4 |
| **最后更新** | 2026-08-17 |
| **后端测试** | 662 passed / 1 skipped（语句 ≥ 90%，分支 ≥ 80%） |
| **维护者** | ethan-peng（Mr-6Lawrence） |
| **Gitee** | https://gitee.com/Mr-6Lawrence/auto-pilot-test |
| **GitHub** | https://github.com/ZipUp-dot/AutoPilot-Test |
