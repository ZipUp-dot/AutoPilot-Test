# 🤖 AutoPilot — AI 驱动的 Web + Android UI 自动化测试平台

> **"让测试回归业务，让 AI 搞定代码。"**
>
> 一个面向测试工程师与开发团队的轻量级、开箱即用的 AI 自动化测试助手。
> 核心理念：**先感知页面，再生成用例**。

> 🎯 **不是又一个 AI 测试框架，而是一个 Excel → 可执行脚本的转化器。**

> 🚨 **运行约束：必须单 uvicorn worker 运行（禁止 `--workers`）**
>
> 批量生成 Job 注册表、停止标志（stop flag）、执行期 Heal 串行化锁、`AI_RATE_LIMIT` / `AI_MAX_CONCURRENCY` 计数均为**进程内**实现。多 worker 会导致批次状态查询落到错误进程（`task_lost`/404）、Stop 失效、限流形同虚设。**请勿使用 `--workers 4` 等多进程参数**；多副本/分布式状态迁移属后续阶段（本版本未引入 Redis）。


## 一、项目定位

AutoPilot 是一个**环境感知型** AI UI 自动化测试平台，同时支持 **Web（Playwright）** 和 **Android（Appium）** 双平台。

传统 AI 生成脚本属于"盲猜式"——AI 不了解页面真实 DOM 结构，导致元素定位频繁失败。AutoPilot 在执行前先用 Playwright / Appium 抓取目标页面的**真实可交互元素**，将元素上下文与测试用例一并喂给 LLM，从而大幅提升首次生成准确率。

**一句话总结**：给 AI 装上"眼睛"，让它基于真实环境写代码，而不是凭空猜测。


## 二、市场定位：跟别人有什么不同？

| 对比维度 | browser-use | Playwright MCP | Bugninja AI | **AutoPilot** |
| :--- | :--- | :--- | :--- | :--- |
| **使用方式** | 写 Python 代码 | 写 Python 代码 | Web 界面（商业 SaaS） | **Web 界面（开源免费）** |
| **面向用户** | 开发者 | 开发者 | QA 团队 | **测试人员（含手工测试）** |
| **输入** | 自然语言指令 | 自然语言指令 | Excel/Word | **Excel 用例** |
| **输出** | AI 实时执行 | AI 实时执行 | 平台内部脚本 | **标准 .py 文件，可下载带走** |
| **平台支持** | Web | Web | 多平台 | **Web + Android** |
| **开源** | ✅ | ✅ | ❌ | ✅ |
| **中文场景优化** | 通用 | 通用 | 部分支持 | **✅ Excel 列名智能匹配** |

**差异化壁垒：**

1. **形态壁垒**：Web 平台 vs 命令行库——测试人员不需要懂 Python
2. **场景壁垒**：Excel 用例转化 vs 自然语言执行——贴合国内测试团队实际工作流
3. **输出壁垒**：生成标准 `.py` 文件——用户不被平台锁定，可自由接入 CI/CD
4. **平台壁垒**：同时支持 Web + Android，统一管理入口


## 三、解决什么问题

| 痛点 | 传统方案 | AutoPilot 方案 |
| :--- | :--- | :--- |
| **编写门槛高** | 要求测试人员具备编程能力 | 零代码，Excel 导入即可生成脚本 |
| **维护成本大** | 前端迭代导致定位器失效，维护占 60%+ 工时 | 智能元素抓取 + 执行容错重试 + AI 自愈，降低维护开销 |
| **用例转化难** | Excel 用例需人工逐条翻译为代码 | 批量导入，AI 自动转化为 Playwright / Appium 代码 |
| **AI 生成不稳定** | 纯自然语言生成，缺乏页面结构上下文 | **环境感知 → 精准生成**，准确率提升显著 |
| **多平台覆盖** | 需独立维护 Web / Android 两套自动化 | 统一平台管理，共享用例、报告、自愈能力 |


## 四、核心业务闭环

AutoPilot 构建了完整的自动化工作流，支持 Web 和 Android 双平台：

```
环境配置 → 元素抓取 → 用例导入 → AI 精准生成 → 可视化执行 → [失败时自愈修复] → 报告输出
```

| 步骤 | Web | Android |
| :--- | :--- | :--- |
| **1. 环境配置** | 输入目标 URL，选择浏览器类型 | 输入 Appium 配置（server、package、activity、device） |
| **2. 智能元素抓取** | Playwright 自动遍历页面，提取所有可交互元素；**goto 失败时 AI 感知导航**（截图分析 → 自动执行前置操作） | Appium 自动遍历 Android 界面，提取元素（resource-id / content-desc / text / XPath） |
| **3. 用例导入** | 上传标准 Excel 测试用例，系统自动识别中英文列名，批量解析 | 与 Web 共享统一格式 |
| **4. AI 精准生成** | 将**真实元素列表** + **用例步骤**作为上下文，生成 Playwright 异步代码 | 生成 Appium 同步代码（链式调用风格），注入监控钩子 |
| **5. 可视化执行与监控** | 支持有头/无头模式运行，Web 界面实时展示执行进度、每步截图与完整日志 | 同步执行，每步截图（before/after），实时状态轮询 |
| **6. 执行容错与自愈** | 失败时自动截图、捕获错误日志，LLM 分析错误上下文，重新生成定位器 | 同步执行，LLM 分析异常类型（NoSuchElement / StaleElement / Timeout / WebDriver），针对性修复 |
| **7. 报告生成** | 自动生成离线 HTML 可视化报告，含通过率、失败详情、截图对比、日志追溯 | 统一报告格式，含平台标签、异常类型分析 |


## 五、产品能力

| 能力 | Web | Android |
| :--- | :--- | :--- |
| **元素定位策略** | 7 级降级（data-testid → id → name → placeholder → class → text → nth-child） | 5 级优先级（resource-id → content-desc → text → class+attributes → XPath） |
| **单条用例端到端耗时** | 标准用例 ≤ 60 秒（实测见 [验收报告](docs/ACCEPTANCE_REPORT.md)；60s 口径以 owner 确认为准） | 标准用例 ≤ 60 秒 |
| **Excel 批量导入** | 单次支持 100 行以上用例无丢失（实测 120 行端到端，见 [验收报告](docs/ACCEPTANCE_REPORT.md)） | 与 Web 共享 |
| **执行模型** | 异步 `async def run_test(page)` | 同步 `def run_test(driver)` |
| **代码风格** | 标准 Playwright Python API | 链式调用 `driver.find_element(...).action()` |
| **监控注入** | AST 注入 `__monitor_before/after`（异步 await） | AST 注入 `__monitor_before/after`（同步，独立定义） |
| **自愈能力** | LLM 重新生成定位器 | LLM 分析异常类型，针对性修复 |
| **开源协议** | MIT | MIT |


## 六、技术栈

| 端 | 技术 |
| :--- | :--- |
| **后端** | FastAPI 0.115 · SQLAlchemy 2.0 · Pydantic 2.9 · Playwright 1.47 · Appium Python Client 4.1 · httpx 0.27 · openpyxl 3.1 · Jinja2 3.1 · Python 3.12+ |
| **前端** | Vue 3 · Vite 5 · Vue Router 4 · Pinia 2 · Element Plus 2.5 · Axios 1.7 · Highlight.js 11.9 |
| **数据库** | MySQL 8.0 |
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
│   │   ├── schemas.py              # Pydantic 响应模型（含 platform + config_json）
│   │   ├── db/                     # SQLAlchemy 引擎 + schema.sql
│   │   ├── models/                 # 9 个 ORM 模型（含 platform/selector_type/metadata/attempts）
│   │   ├── routers/                # 7 个 API 路由模块
│   │   ├── services/               # 11 个业务服务（含编排器、AppiumService、AndroidCrawlService、停止控制）
│   │   ├── utils/                  # Excel 解析 / AST 校验 / 注入 / 截图 / Appium 代码注入 / AI 限流
│   │   ├── prompts/                # AI Prompt 模板（代码生成/自愈/页面分析 共 5 个）
│   │   ├── templates/              # HTML 报告模板
│   │   └── middlewares/            # 请求日志 + 响应计时
│   ├── tests/                      # pytest 四层测试套件（1451 测试 / 1449 passed，覆盖率 90%）
│   │   ├── conftest.py             # 共享 Fixture（SQLite 内存库 + 全 Mock）
│   │   ├── factories.py            # 工厂类
│   │   ├── unit/                   # 单元测试
│   │   ├── services/               # 服务层测试
│   │   ├── routers/                # 路由层测试
│   │   └── integration/            # 端到端集成测试
│   ├── uploads/                    # 截图 / Excel / 视频
│   ├── reports/                    # HTML 报告（30 天自动清理）
│   ├── pytest.ini                  # pytest 配置
│   └── requirements.txt
└── frontend/                       # 前端管理界面（Vue 3）
    ├── src/
    │   ├── api/                    # 8 个 API 模块
    │   ├── components/             # 8 个公共组件
    │   ├── composables/            # 轮询 / WebSocket
    │   ├── stores/                 # 4 个 Pinia Store
    │   ├── router/                 # 路由配置
    │   ├── styles/                 # 全局样式
    │   └── views/                  # 视图页面
    ├── Dockerfile                    # 多阶段构建
    └── nginx.conf                    # 反向代理 + SPA 回退
```

### 架构事实源（9 事实表 + 两层代码 Resolver）

AutoPilot 的终态语义只从**冻结的事实表**读取，禁止运行期重算或向旧版本回退。9 张事实表（`projects` 为可变配置表，不作为终态事实源——执行期环境只读 Manifest 快照）：

| # | 事实表 | 承载的权威事实 |
| :-- | :--- | :--- |
| 1 | `test_cases` | 用例步骤（`steps` + `hash_steps` 规范化哈希，作为执行前 Drift 比对基准） |
| 2 | `page_elements` | 元素抓取结果（每次 crawl 全量重建） |
| 3 | `generated_codes` | AI 生成代码（`is_valid` / `source_steps_hash` / `is_mock`，append-only） |
| 4 | `executions.manifest_json` | Admission 冻结的执行环境与 per-case 快照（target_url / browser_type / execution_mode / ssrf_policy / cases） |
| 5 | `executions.runtime_state_json` | per-case 运行期与终态事实源（`active_code_id` + `case_status` + `terminal_reason`，Seal 时一次写死） |
| 6 | `execution_steps` | 步骤级事实（状态 / 截图 / 日志 / `error_type` / `duration_ms`），Seal 后不可变 |
| 7 | `execution_reports` | 报告事实（`generation_status` + `claim_token` fencing） |
| 8 | `heal_records` | 自愈事实（`UNIQUE(execution_id, case_id, round_no)` 竞态 claim、round 级记录） |
| 9 | `batch_cases` / `batch_records` | 生成期逐 Case 终态事实（terminal 后不可变）+ 批级 KPI summary |

**两层代码 Resolver（代码来源钉死，禁止 latest 后门）**

1. **准入层 `get_effective_code`**：仅用于批量生成未覆盖的入口（Execute Only / Manual）——取该 Case 集合内唯一 latest，校验 `source_steps_hash`、`is_mock=0`，并以当前 `CodeValidator` 做 validate-on-load；任何一步失败都**不得**继续向旧版本搜索。
2. **运行期层 `ExecutionCodeResolver`**：Execution 物化后，执行 / 自愈 / 报告一律只读 `runtime_state[case_id].active_code_id`（Admission 冻结，等于 `manifest.cases[case_id].original_code_id`），禁止回退 latest。

**执行隔离 = 应用级受限执行（非 OS 级容器沙箱）**：AI 生成代码在受限命名空间内执行——白名单 builtins + `SafePlaywright`（`__slots__` + `__getattribute__` 白名单）+ AST 层拦截 `eval` / `exec` / `open` / `import` 与下划线属性。这是应用层的能力收口，不等同于操作系统级隔离（容器沙箱属后续阶段规划）。


## 八、项目价值

### 对个人开发者
- **面试核心竞争力**：展示全栈开发 + AI 工程化落地 + 测试工具产品设计的复合能力
- **开源作品集**：一个完整的、可运行的开源项目，是面试时最有力的技术信任背书
- **技术深度**：实践 Prompt Engineering、Playwright + Appium 自动化、异步 FastAPI、现代前端工程化
- **开源影响力**：为国内 "AI + 测试" 社区贡献可落地的轻量级方案

### 对团队与企业
- **降低门槛**：手工测试人员无需编程即可参与 UI 自动化
- **提升效率**：用例转化从"人工编写"变为"AI 精准生成"
- **减少维护**：元素抓取提升定位器稳定性，AI 自愈修复覆盖异常，降低脚本维护成本
- **统一管理**：Web + Android 双平台，一个入口统一管理


## 九、迭代路线图

| 版本 | 状态 | 核心内容 |
| :--- | :--- | :--- |
| **V1.0** | ✅ MVP 已发布 | 跑通"抓取 → 导入 → 生成 → 执行 → 容错重试 → 报告"全链路（Web 端） |
| **V1.1** | ✅ Core 已完成 | 新增 Android 支持（AppiumService、元素抓取、AI 生成、执行、自愈、监控）、Orchestrator 平台分发、Heal History、Report 增强、Project/PageElement 平台隔离 |
| **V1.2** | 🔧 开发中 | AI 感知页面抓取（goto 失败自动截图分析并执行前置操作）、自愈成本防护（入口健康检查 / 同类错误快速失败 / AI 调用限流熔断）、执行前环境健康检查、执行列表实时聚合、测试覆盖率 90%（1451 测试 / 1449 passed） |


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
# 一键初始化（Windows 用 PowerShell 执行 \scripts\setup.ps1）
./scripts/setup.sh
# 等价于：
#   python -m venv backend/.venv
#   pip install -r backend/requirements.txt -r backend/requirements-dev.txt
#   playwright install chromium        # 安装与 playwright==1.56.0 对应的浏览器 revision
#   (cd frontend && npm ci)            # 前端按 lockfile 精确安装

# 后端（显式单 worker；禁止 --reload —— IDE 沙箱会拦截 Playwright 子进程）
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm run dev   # http://localhost:5173
```

> 生产依赖见 `backend/requirements.txt`；测试依赖（pytest 等）见 `backend/requirements-dev.txt`。
> 前端依赖由 `package-lock.json` 精确锁定，Docker 与 setup 脚本均使用 `npm ci`。

### 运行测试

```bash
cd backend
.venv/bin/python -m pytest           # 运行全部测试（含覆盖率）
.venv/bin/python -m pytest tests/unit/          # 仅单元测试
.venv/bin/python -m pytest tests/services/      # 仅服务层测试
.venv/bin/python -m pytest tests/routers/       # 仅路由层测试
.venv/bin/python -m pytest tests/integration/   # 仅端到端集成测试
```

测试套件采用**四层架构**（unit / services / routers / integration），全部运行于 SQLite 内存数据库、零外部依赖：
- LLM API、Playwright、Appium、文件系统均通过 Mock 隔离
- 当前 **1449 passed, 2 skipped**（collected 1451），语句覆盖率 **91%**（含分支总覆盖率 **90%**）
- 完整说明见 [tests/README_TEST.md](backend/tests/README_TEST.md)

> **测试数字同源约束**：上述数字来自 **Release R_P2-rc @ commit `7862736`** 的**同一次** `cd backend && python -m pytest` 全量执行，原始输出为 [backend/test_output.txt](backend/test_output.txt)；README 只引用该文件，**不存在多处分别填写**。

### Release 验收（四项量化指标实测）

四项指标（首生成有效率 / 最终执行成功率 / 单条用例端到端耗时 / Excel 批量导入）的实测数字、口径说明、与立项书 3.2 的差距分析详见 **[docs/ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md)**。

| 指标 | 实测（R_P2-rc @ `7862736`） |
| :--- | :--- |
| 首生成有效率（工程代理指标） | 99/99 = 100.0%（cohort 覆盖 99/120；另有 21 例因单 Case 生成预算耗尽被剔除） |
| 最终执行成功率（Case 级） | 68/99 = **68.7%（未达到 85%）** |
| 单条用例端到端耗时 | 执行阶段步骤耗时 P95 = 31.7s；含自愈 wall-clock ≈ 67.6s/Case（**60s 口径以 owner 确认为准**） |
| Excel 批量导入 | 120 行端到端（导入 120/120 → 入库 → 生成 → 执行不破坏） |

> 60s 指标的**口径修订仅作为建议**提交项目 owner 决策（见验收报告 §6）；在 owner 确认前，本 README 不改变立项书原口径表述。

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
| **当前版本** | V1.2（开发中） |
| **文档版本** | V3.2 |
| **最后更新** | 2026-09-26 |
| **后端测试** | 1449 passed / 2 skipped（语句覆盖率 91%，含分支 90%）—— Release R_P2-rc @ commit `7862736` |
| **维护者** | ethan-peng（Mr-6Lawrence） |
| **Gitee** | https://gitee.com/Mr-6Lawrence/auto-pilot-test |
| **GitHub** | https://github.com/ZipUp-dot/AutoPilot-Test |