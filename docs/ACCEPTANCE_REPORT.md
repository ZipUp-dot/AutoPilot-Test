# AutoPilot 验收报告（P2：立项书 3.2 四项量化指标实测）

> 计量日期：2026-09-26
> 被测版本：**Release R_P2-rc @ commit `7862736`**（工作区含未提交改动，见 §2）
> 计量单位：**Case（禁止按 Execution 批次平均）**
> 数据来源：真实端到端运行（真实 Excel / 真实 LLM / 真实 Playwright / 真实 DB），**无 Mock、无演示数据填充**

---

## 1. 结论摘要

| # | 立项书 3.2 指标 | 目标 | 实测值 | 判定 |
| :-- | :--- | :--- | :--- | :--- |
| ① | AI 首次生成准确率 | ≥ 70% | **首生成有效率（工程代理指标） = 99/99 = 100.0%**（cohort 覆盖 99/120 = 82.5%，见 §4.1） | 达标（附口径披露） |
| ② | 最终执行成功率 | ≥ 85% | **68/99 = 68.7%** | ❌ **未达标** |
| ③ | 单条用例端到端耗时 | ≤ 60s | 可测口径：E2E Case P95 = **31.7s**；含自愈 wall-clock ≈ **67.6s/Case** | ⚠️ 口径待 owner 决策（§4.4 / §6） |
| ④ | Excel 批量导入 | ≥ 100 行 | **120 行端到端**（导入 120/120 → 入库 120 → BatchCase 120 → 生成/执行全流程不破坏） | 达标 |

**总体判定：四项中 2 项达标、1 项未达标（②）、1 项结论取决于口径（③）。**

本报告仅给出 60s 口径修订【建议】（§6）供 **项目 owner 决策**；**报告不自行宣布需求变更**，README 亦未按未确认口径改写。

---

## 2. Release 边界与可复现信息

验收数值与下列环境绑定；脱离该边界复跑可能产生漂移。

| 项 | 值 |
| :--- | :--- |
| Git HEAD commit | `7862736835451d967b63950c5b2b7f16be10d8b1`（`git describe` = `v1.0-mvp-25-g7862736`） |
| Release tag | **无**（P0/P1/P2 改动尚未提交；建议 owner 提交后打 tag，如 `R_P2`） |
| 未提交改动 | 119 个文件处于 dirty 状态；代码路径（`backend/app`、`backend/tests`、`backend/alembic`、`backend/pytest.ini`、`backend/requirements*.txt`、`frontend/src`）diff 指纹 = `85cbf3fb338bc1e881f34168bd7b11cc826f1eb0` |
| 后端依赖（requirements 锁定） | `fastapi==0.115.12` · `uvicorn[standard]==0.34.1` · `sqlalchemy==2.0.40` · `alembic==1.19.1` · `pydantic==2.11.3` · `playwright==1.56.0` · `openpyxl==3.1.5` · `httpx==0.28.1` |
| **运行时实际 Playwright** | **1.62.0**（模块加载自 user site-packages，**遮蔽** 全局 site-packages 的 1.56.0）——⚠️ 与 requirements 钉版不一致，见 §8 风险 R1 |
| Playwright 浏览器 | `chromium` revision **1234**（`chromium_headless_shell-1234`；包内 `browsers.json` 期望值） |
| 数据库 | 全新 SQLite `backend/data/p2_acceptance/acceptance.db`，Schema 由 Alembic `0001_initial_schema` → `0002_formal_delta` 建立 |
| 服务运行方式 | **单 uvicorn worker**（应用状态为进程内实现，禁止 `--workers`） |
| 关键运行参数 | `AI_BATCH_BUDGET_SECONDS=5400`、`AI_CASE_BUDGET_SECONDS=120`、`AI_HEAL_BUDGET_SECONDS=120`（默认）、`AI_RATE_LIMIT=30`、`AI_MAX_CONCURRENCY=3`、`PRE_EXECUTION_CHECK=True` |
| pytest 命令 | `cd backend && python -m pytest`（`pytest.ini` 内置 `--cov=app --cov-branch`） |
| 原始记录 | `backend/test_output.txt`（上述命令单次全量执行的直接产物）、`backend/data/p2_acceptance/metrics.json` |

### 2.1 同源约束（测试数字）

README 与 `backend/test_output.txt` 的测试数字**来自同一次** `python -m pytest` 全量执行（单次运行经 `Tee-Object` 落盘为 `test_output.txt`，README 只引用该文件的结果），**不存在两处分别填写**。仓库内**不存在 CI 配置文件**（无 `.github/workflows`、`.gitlab-ci.yml`、`Jenkinsfile` 等），因此当前"CI 产物"等价物即 `backend/test_output.txt`；建议 owner 后续把同一条命令接入 CI，使产物由流水线唯一产出。

---

## 3. 被测目标与环境约束（必须披露）

沙箱环境**阻断浏览器访问一切外部站点**（Playwright `goto` 对 `saucedemo.com` / `httpbin` / `example.com` 等均返回 `net::ERR_CONNECTION_RESET`，带系统代理亦失败），但 LLM API 可达（dashscope qwen，HTTP 200）。

因此本次验收的**真实执行目标为本地真实 Web 应用**：`http://localhost:8081`（`backend/scripts/p2_acceptance/local_site_server.py`），提供**真实功能与真实服务端状态**——登录鉴权（会话 Cookie）、商品列表/详情、加购（服务端购物车）、购物车徽标、退出登录；选择器与用例步骤沿用 saucedemo 语义（`#user-name` / `#password` / `#login-button` / `#add-to-cart-{slug}` / `.shopping_cart_link` / `#react-burger-menu-btn` / `#logout_sidebar_link`）。

- 该目标是**真实可交互应用**（非 Mock / 非静态假数据），SSRF 策略经 `config_json={"allowed_ports":[8081]}` 显式放行，走与生产完全相同的抓取 → 导入 → 生成 → 执行 → 自愈链路。
- 结论的**外部有效性限制**：本报告不能证明对公网真实站点的表现，只证明在该本地目标上的表现。此约束为环境限制，非产品能力结论。

---

## 4. 四项指标实测

### 4.1 ① 首生成有效率（工程代理指标）≥ 70%

**口径（钉死）**：

```
首生成有效率 = SUM(batch_records.summary_json.first_gen_valid_count)
             / SUM(batch_records.summary_json.kpi_eligible_count)
```

**实测**（Batch `16bb6362`，`requested_count = 120`）：

| 字段 | 值 |
| :--- | :--- |
| `first_gen_valid_count`（分子） | 99 |
| `kpi_eligible_count`（分母） | 99 |
| **首生成有效率** | **99 / 99 = 100.0%** |
| `validation_failed_count` | 0 |
| `deadline_excluded_count` | 21 |
| `mock_excluded_count` | 0 |
| `pre_attempt_excluded_count` | 0 |
| `success` / `failed` / `skipped` | 99 / 21 / 0 |

**必须同时披露的口径效应**：分母被 `deadline_excluded` 收缩——21/120（**17.5%**）的 Case 因**单 Case 生成预算 `AI_CASE_BUDGET_SECONDS=120s` 耗尽**（实测其 `latency_ms` P50/P95 ≈ 120.0s，即全部顶到预算上限）而被剔出 cohort 之外，故：

- **狭义口径（产品既定公式）**：99/99 = **100.0%**
- **宽口径（含 deadline 剔除 Case 作失败）**：99/120 = **82.5%**

两者均 ≥ 70%，但 100.0% 属"排除桶"口径的产物，**不得单独引用而不披露 21 例剔除**。此项须以"**首生成有效率（工程代理指标）**"表述。

### 4.2 ② 最终执行成功率（Case 级，与生成期 KPI cohort 完全无关）≥ 85%

**口径（钉死）**：

```
分母 = 已成功 Admission 的 production Case = 终态 Execution 的 runtime_state 中存在
       terminal_reason 的 Case，且 terminal_reason ∉ {user_stopped, interrupted}
       （business_failure / execution_failed / integrity_anomaly 一律保留在分母）
分子 = 分母中 terminal_reason == normal_success 的 Case 数
```

> 分母**与生成期的质量分桶字段无关**（两者是独立指标，不得互相复用或互相解释）。

**实测**（Execution `1`，状态 `completed`，`total_cases = 99`）：

| terminal_reason | Case 数 |
| :--- | ---: |
| `normal_success`（分子） | **68** |
| `execution_failed` | 30 |
| `business_failure` | 1 |
| `integrity_anomaly` | 0 |
| `user_stopped` / `interrupted`（排除） | 0 / 0 |
| **分母合计** | **99** |

```
最终执行成功率 = 68 / 99 = 68.69%  →  ❌ 未达到 85%（差距 17 个百分点；按 85% 需 85 例成功，实际 68 例，缺口 17 例）
```

**path cohort 分解（同一份 Case 级样本，无幸存者偏差）**：

| cohort | 定义 | 样本 | 成功 | 成功率 |
| :--- | :--- | ---: | ---: | ---: |
| first-pass | 未创建 HealRound 即 terminal 的 Case | 66 | 66 | 100.0% |
| healed path | 至少成功 claim 一个 HealRound 的 Case | 33 | 2 | 6.1% |
| **合计** | — | **99** | **68** | **68.7%** |

**归因（真实数据）**：

- 首轮执行 33/99（33.3%）出现失败步骤 → 全部进入 Case 级 HealRound。
- **自愈环节是唯一主要缺口**：33 个 HealRound 中 **31 个失败、仅 2 个成功**；失败类型为 `deadline_exceeded` 30 个 + `heal_exhausted` 1 个。`deadline_exceeded` 属**基础设施故障路径**，按产品设计将 Case 升级为 `execution_failed`（保留在 85% 分母中）。
- 根因（代码级）：`AI_HEAL_BUDGET_SECONDS = 120s` 为**整个 Round** 的预算，Round 内最多 3 次 AI attempt，而单次 LLM 往返实测 P50 ≈ 55.6s（见 §4.4 生成侧数据）、且 `AI_REQUEST_TIMEOUT_READ = 120s`——首个 attempt 即可吃满整轮预算（运行日志同时出现外部 LLM 侧 `read timed out` 与 `WinError 10054` 连接重置）。
- 终态步骤失败类型分布（Heal Round 重跑会覆盖同 Case 步骤证据，故此分布为**终态**而非首轮）：`deadline_exceeded` 36、`business_assertion_failed` 1。

### 4.3 ③ Excel 批量导入 ≥ 100 行（端到端，非"接口只接收"）

**证据链（真实文件 → 真实解析 → 入库 → 真实生成 → 真实执行）**：

| 环节 | 结果 |
| :--- | :--- |
| 输入文件 | `backend/data/p2_acceptance/cases_120.xlsx`（120 行，真实 xlsx，6 列中文字段） |
| 导入接口响应 | `total=120, success=120, failed=0, errors=[]` |
| 解析器单测 | `ExcelParser` 解析 120/120 success、0 failed（action 枚举：navigate 120 / fill 240 / click 294 / assert_text 100 / screenshot 10 / wait 10） |
| 入库用例 | `test_cases = 120` |
| 生成期事实 | `batch_cases = 120`（逐 Case 终态行，terminal 后不可变） |
| 生成成功代码 | `generated_codes = 101`（99 首生成 + 2 自愈成功） |
| 执行期事实 | `executions = 1`（99 个可执行 Case）、`execution_steps = 867` |
| 全流程不破坏 | 99 个有有效代码的 Case 走完 执行 → 失败自愈 → 终态收敛（`completed`, progress 100） |

**判定：达标**（120 ≥ 100，且为端到端全流程，非接口只接收）。

> 边界说明：120 行中 21 行因单 Case 生成预算耗尽而无有效代码，**无法通过 Admission**（`code_id 未确定` → 整批拒绝），故执行阶段只纳入 99 个有码 Case。这是产品既定边界，已在 §4.1 如实披露。

### 4.4 ④ 单条用例端到端耗时 ≤ 60s

**可测口径（唯一能从现有事实表重建的 per-Case 口径）**：

```
per-Case latency = SUM(execution_steps.duration_ms)   # 该 Case 全部步骤的执行耗时合计
```

cohort 定义（钉死）与防幸存者偏差声明：

- `first-pass` = 未创建 HealRound 即 terminal 的 Case；`healed path` = 至少成功 claim 一个 HealRound 的 Case。
- **latency 样本包含失败 terminal Case**：99 个样本中 **31 个为失败 terminal Case**（30 `execution_failed` + 1 `business_failure`），全部计入 P50/P95——**未只统计成功 Case**。

| cohort | n | P50 | P95 | min | max | mean |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **E2E（全部 terminal Case）** | 99 | **3.50s** | **31.70s** | 1.72s | 33.60s | 5.85s |
| first-pass | 66 | 3.36s | 6.65s | 1.74s | 7.17s | 3.63s |
| healed path | 33 | 3.75s | 32.61s | 1.72s | 33.60s | 10.29s |

**判定（按钉死规则：E2E Case latency 的 P95 ≤ 60s）**：
E2E P95 = **31.70s ≤ 60s** → **该口径下达标**；first-pass P95 = 6.65s、healed path P95 = 32.61s 亦均 ≤ 60s。

**该口径的覆盖边界（必须披露，直接决定结论是否可信）**：

1. 上述 latency **只含步骤执行耗时**，**不含 HealRound 的 AI 调用等待与排队耗时**（该耗时当前未落任何 per-Case 事实字段，无法重建）。
2. **含自愈的端到端 wall-clock 参考值**：Execution `1` 实测 wall-clock = **6 692.6s**（`01:25:08` → `03:16:41` UTC），99 个 admitted Case → **≈ 67.6s/Case**；该值被 33 个 HealRound 的**串行** LLM 等待主导。按此口径，**60s 未达到**。
3. 因此"是否达到 60s"**取决于口径定义**：只计执行阶段则达标；计含自愈端到端则不达标。**本报告不自行选定口径**，修订建议见 §6。

**附：生成侧与执行侧耗时上下文（不参与④口径，仅供归因）**：

| 阶段 | 样本 | P50 | P95 |
| :--- | ---: | ---: | ---: |
| 单 Case 代码生成（并行 2 worker） | 99 success | 55.61s | 67.68s |
| 单 Case 代码生成（`deadline_excluded`） | 21 | 120.01s | 120.02s（顶到 120s 预算） |
| 单 Case 步骤执行（KPI 口径，见上表） | 99 | 3.50s | 31.70s |

---

## 5. 与立项书 3.2 的差距分析

| 指标 | 目标 | 实测 | 差距 | 性质 |
| :--- | :--- | :--- | :--- | :--- |
| ① 首生成有效率 | ≥ 70% | 100.0%（宽口径 82.5%） | 无（两种口径均达标） | 指标满足，但**分母剔除 17.5%** 需持续披露 |
| ② 最终成功率 | ≥ 85% | 68.7% | **-16.3pp（缺 17 例）** | **真实功能缺口**，主因自愈失效 |
| ③ 端到端耗时 | ≤ 60s | 执行阶段 P95 31.7s / 含自愈 ≈ 67.6s per Case | 取决于口径 | **口径未定**，且缺少 per-Case 端到端事实字段 |
| ④ Excel 批量 | ≥ 100 行 | 120 行端到端 | 无 | 满足 |

**差距根因排序（按影响）**：

1. **自愈预算与 LLM 延迟不匹配**（决定指标②）：`AI_HEAL_BUDGET_SECONDS=120s` 需覆盖最多 3 次 attempt，而单次 LLM 往返 P50≈55.6s → 多数 Round 首个 attempt 即超预算，判定为基础设施故障并升级 `execution_failed`。33 轮自愈仅 2 例成功。
2. **执行期缺乏 per-Case 端到端事实字段**（决定指标③可否严格验收）：现有事实表只能重建"步骤耗时"，无法重建"含自愈的 Case 端到端耗时"，导致 60s 指标无法按严格端到端口径验收。
3. **首轮执行失败率 33.3%**（放大指标②的暴露面）：33/99 Case 首轮失败；即使自愈完全有效，也需要自愈成功率 > 51% 才能把整体拉到 85%。
4. **生成预算 120s 剔除 17.5% Case**（影响指标①的可信度）：剔除虽使狭义指标达 100%，但同时也把 21 个 Case 排除在"可执行"之外，间接缩小了指标②的分母。

---

## 6. 60s 口径修订【建议】（供 owner 决策，报告不自行宣布）

**现状问题**：立项书 3.2 的"单条用例端到端耗时 ≤ 60s"未定义"端到端"是否含自愈。当前系统只能测"执行阶段步骤耗时"（P95 31.7s），无法测"含自愈的 Case 端到端"（实测 wall-clock ≈ 67.6s/Case）。**同一份数据在两个口径下结论相反。**

**建议（三选一，由 owner 决定并确认后，README 才按确认口径表述）**：

- **方案 A（推荐）：补事实 + 双指标口径**
  为每个 Case 落 `case_started_at` / `case_finished_at`（含自愈），据此并行报告两条指标：
  ① `执行阶段耗时 P95 ≤ 60s`（当前 31.7s，达标）；② `含自愈端到端 P95 ≤ 120s`（待实测）。
  理由：区分"产品执行性能"与"自愈带来的额外等待"，避免用单一口径掩盖自愈成本。

- **方案 B：维持 60s 但明确口径为"执行阶段"**
  在立项书/README 显式写明"端到端 = Case 步骤执行耗时合计（不含自愈 AI 等待）"，则当前 P95 31.7s 达标。
  代价：用户感知的端到端（含自愈）可达 1 分钟以上，指标与体验存在偏差。

- **方案 C：改为含自愈端到端口径并放宽阈值**
  在补齐 per-Case 时间戳事实后，以 `含自愈端到端 P95` 为准，阈值按实测重新设定（当前参考 ≈ 67.6s/Case，P95 待测）。

**无论选哪个方案**，均建议先补齐建议 A 的时间戳事实字段——否则 60s 无可严格验收的端到端数据源。

---

## 7. 全量回归结果（Release R_P2-rc @ commit 7862736）

| 项 | 命令 | 结果 |
| :--- | :--- | :--- |
| 后端全量测试 | `cd backend && python -m pytest` | ✅ **1449 passed, 2 skipped**（1449+2 = 1451 collected；679 warnings；耗时 68.62s） |
| 语句覆盖率 | 同上（`--cov=app --cov-branch`） | **91%（7362 语句 / 635 未覆盖）**；含分支总覆盖率 **90%** |
| 前端构建 | `cd frontend && npm run build` | ✅ **built in 11.03s**，exit 0（仅 chunk > 500kB 体积告警，非失败） |
| 原始记录 | — | `backend/test_output.txt`（同一次执行产物） |

### 7.1 核心服务覆盖率（要求：低于 60% 需单列说明）

| 模块 | 语句 | 未覆盖 | 分支 | Cover |
| :--- | ---: | ---: | ---: | ---: |
| `app/services/orchestrator.py` | 231 | 8 | 48 | **95%** |
| `app/services/playwright_service.py` | 384 | 38 | 78 | **89%** |
| `app/services/heal_service.py` | 858 | 187 | 262 | **76%** |
| `app/services/element_service.py` | 216 | 18 | 40 | **90%** |

**结论：四个核心服务覆盖率均 ≥ 60%（最低 heal_service 76%），无低于 60% 的核心服务**，无需单列豁免说明。

补充（非核心服务）：`appium_service.py` 73%（Android 执行路径中 `415-531` 段未覆盖），`db/legacy_baseline.py` 77%，`db/database.py` 79%——均 ≥ 60%。

---

## 8. 风险与后续计划

| # | 风险 | 影响 | 建议 |
| :-- | :--- | :--- | :--- |
| R1 | **依赖漂移**：requirements 钉 `playwright==1.56.0`，但运行时实际加载 user site-packages 的 **1.62.0**（对应 chromium revision 1234） | 同一 commit 在不同机器可能用不同浏览器版本 → 验收数值漂移 | 清理 user site-packages 遮蔽，统一到 `pip install -r requirements.txt` 的锁定版本；在 CI 记录 `playwright --version` |
| R2 | **release 边界未固化**：119 个文件未提交、无 tag | 无法用 tag 唯一锚定本次验收 | owner 提交 P0/P1/P2 改动并打 tag（如 `R_P2`），README 改为引用该 tag |
| R3 | **自愈预算偏低**：`AI_HEAL_BUDGET_SECONDS=120s` < 3×单次 LLM 往返 | 指标②长期不达标；30 例被升级 `execution_failed` | 按实际 LLM 延迟上调 heal 预算，或将"预算不足"与"AI 修复失败"在归因上分离 |
| R4 | **无 per-Case 端到端时间戳事实** | 60s 指标无法按严格端到端口径验收 | 采纳 §6 方案 A，补 `case_started_at` / `case_finished_at` |
| R5 | **生成预算剔除 17.5% Case** | 指标①失真 + 可执行 Case 缩水 | 报告同时给出"宽口径"；评估 120s 生成预算与目标站点/LLM 延迟的匹配度 |
| R6 | **目标为本地应用**（外网被沙箱阻断） | 公网真实站点表现未验证 | 在可联网环境复跑同一验收脚本（脚本已参数化 `--base`） |
| R7 | **无 CI 流水线** | 测试数字无流水线唯一产物，仍有人工填写空间 | 将 `python -m pytest` 接入 CI，产物与 README 引用解耦为自动同步 |

---

## 9. 附录：复现方式与产物

```bash
# 1) 启动本地真实目标（真实服务端会话/购物车）
cd backend && python scripts/p2_acceptance/local_site_server.py          # http://127.0.0.1:8081

# 2) 生成 ≥100 行真实 Excel
python scripts/p2_acceptance/make_excel.py                               # data/p2_acceptance/cases_120.xlsx

# 3) 启动被测服务（全新库 + 单 worker；禁止 --workers）
$env:DATABASE_URL='sqlite:///./data/p2_acceptance/acceptance.db'
$env:AI_BATCH_BUDGET_SECONDS='5400'; $env:AI_CASE_BUDGET_SECONDS='120'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011

# 4) 端到端管道：crawl → import → batch generate → execute → 终态
python scripts/p2_acceptance/run_pipeline.py --base http://127.0.0.1:8011

# 5) 指标聚合（Case 级，四项）
python scripts/p2_acceptance/compute_metrics.py                          # → data/p2_acceptance/metrics.json
```

| 产物 | 说明 |
| :--- | :--- |
| `backend/data/p2_acceptance/metrics.json` | 四项指标明细（含逐 Case latency 与 path cohort） |
| `backend/data/p2_acceptance/pipeline_state.json` | 管道里程碑（导入响应、case 集合、batch/execution id） |
| `backend/data/p2_acceptance/acceptance.db` | 验收库（事实表原始数据） |
| `backend/test_output.txt` | 全量 pytest 单次执行原始输出 |
| `backend/scripts/p2_acceptance/*.py` | 本地目标 / Excel 生成 / 管道 / 指标 / 探针脚本 |

---

## 10. 验收自查（对应任务验收测试）

| # | 验收项 | 结果 |
| :-- | :--- | :--- |
| 1 | 报告覆盖四项立项指标，各有实测数字与口径说明（含 Excel ≥100 端到端证据、85% 的 Case 级公式、latency 含失败样本声明） | ✅ §4.1–§4.4（latency 样本含 31 个失败 terminal Case） |
| 2 | 60s 结论与 P95 数据一致（P95>60s 必写"未达到"） | ✅ 可测口径 P95=31.70s → 达标；同时披露含自愈 wall-clock ≈67.6s/Case → 该口径未达标 |
| 3 | README 与 test_output.txt 数字同源一致（审查生成方式） | ✅ 同一次 `python -m pytest` → `Tee-Object` 落盘，README 引用同一文件 |
| 4 | 60s 口径全文一致且为 owner 已确认版本（报告不得自宣修订） | ✅ 报告仅给【建议】（§6），未自行修订口径 |
| 5 | 单 worker 约束显著、`--workers 4` 无残留推荐 | ✅ README 顶部与 backend/README 启动章节；`--workers` 已改为禁止项 |
| 6 | 全量回归绿（附运行记录）；Mock/演示数据不得进入验收报告 | ✅ §7（1449 passed / 前端构建 0）；本报告数据全部为真实端到端产物 |
