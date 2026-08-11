直接复制以下内容，替换你根目录的 `README.md` 即可：

```markdown
# 🤖 AutoPilot — AI 驱动的智能 Web UI 自动化测试平台

> **“让测试回归业务，让 AI 搞定代码。”**

**核心理念**：先感知页面，再生成用例。

**核心差异化**：不用写代码，不用配环境——把 Excel 历史用例直接变成可执行的自动化脚本。

> 🎯 **不是又一个 AI 测试框架，而是一个 Excel → 可执行脚本的转化器。**


## 一、项目定位

**AutoPilot 是什么？**

一个面向**测试人员**的 Web 平台。上传历史 Excel 用例，点击生成，拿到可执行的 Playwright `.py` 文件。

**AutoPilot 解决了什么问题？**

国内 80% 的测试团队，历史用例都躺在 Excel 里。手工翻译成自动化脚本，效率低、成本高、维护难。AutoPilot 把“Excel → 可执行脚本”这件事自动化了。


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
| **编写门槛高** | 要求测试人员会编程 | 零代码操作，Excel 导入即可生成脚本 |
| **维护成本大** | 定位器失效需手工修复 | 先抓取真实页面元素，再让 AI 写代码；执行失败自动重试 |
| **用例转化难** | 人工逐条翻译 | 批量导入，AI 自动转化为 Playwright 代码 |
| **AI 生成不稳定** | 纯自然语言生成，AI 盲猜页面结构 | **先感知页面，再生成用例** |


## 四、核心业务闭环

```
创建项目 → 智能元素抓取 → 导入 Excel 用例 → AI 生成脚本 → 可视化执行 → 容错重试 → 下载报告
```


## 五、产品能力

| 能力 | 说明 |
| :--- | :--- |
| **元素定位准确率** | 基于真实 DOM 上下文生成定位器，设计值 ≥ 70% |
| **单条用例端到端耗时** | 标准用例、网络正常情况下 ≤ 60 秒 |
| **Excel 批量导入** | 单次支持 100 行以上用例无丢失 |
| **开源协议** | MIT |


## 六、项目价值

### 对测试团队
- 手工测试人员无需编程即可参与 UI 自动化
- Excel 用例从“人工编写”变为“AI 批量生成”
- 基于真实元素生成定位器，降低脚本维护成本

### 对个人开发者
- 展示全栈 + AI 工程化 + 产品设计的复合能力
- 完整的开源作品，面试时最有力的技术信任背书


## 七、快速开始

### 效果演示

<p align="center">
  <img src="demo/MVP_DEMO.gif" width="800" alt="AutoPilot 演示">
</p>

### 前置条件

- Docker 20.10+（已内置 Docker Compose）

### 一键启动

```bash
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

首次构建约 5~8 分钟，完成后访问 http://localhost:8080 即可使用。

> 详细技术文档、API 接口、数据库设计请参阅：
> - [📁 后端文档](backend/README.md)
> - [📁 前端文档](frontend/README.md)


## 八、迭代路线图

| 版本 | 状态 | 核心内容 |
| :--- | :--- | :--- |
| **V1.0** | ✅ MVP 已发布 | 抓取 → 导入 → 生成 → 执行 → 容错重试 → 报告 |
| **V1.1** | 📅 规划中 | AI 自愈升级（LLM 分析失败原因并重写定位器）、更丰富的测试对比报告 |
| **V2.0** | 📅 规划中 | CI/CD 定时任务、用户权限管理、APP UI 自动化支持（扩展 Appium） |


## 九、贡献指南

欢迎 Star、Fork 与贡献代码！如有问题，请提交 Issue 或联系维护者。


## 📄 开源许可

MIT


## 📌 版本信息

| 项目 | 内容 |
| :--- | :--- |
| **当前版本** | V1.0 MVP |
| **维护者** | ethan-peng（Mr-6Lawrence） |
| **Gitee** | https://gitee.com/Mr-6Lawrence/auto-pilot-test |
| **GitHub** | https://github.com/ZipUp-dot/AutoPilot-Test |
```