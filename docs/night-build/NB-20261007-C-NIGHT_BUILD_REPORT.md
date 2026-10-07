# NIGHT BUILD REPORT · nb-20261007-C（Build-C）

> docs/night-build/ 归档 · 2026-10-07 · **素材稿**（施工方产出，待 Kimi 定稿归档）
> Owner 拥有最终签字权；终态一律以 docs/night-build/nb-20261007-C-STATE.json 为准。
> 血统：QUEUE-SNAPSHOT-nb-20261007-C · SPEC-PROJ-V20-SCHED/CICD/MOCK-v1（均 FROZEN 2026-10-07 15:25）
> · FEATURE-ACCEPTANCE-C1-SCHED / C2-CICD / C3-MOCK

## 1. Frozen Spec
V9.8.1 @ docs/FROZEN_SPEC_V9.8.1.md，content sha256 `2d9bb143…a908`（Bootstrap 已锁，Gate A PASS）

## 2. Freeze Manifest
Feature Specs（FROZEN 2026-10-07 15:25，随梭哈批），三件均是冻结后核验 MATCH：
- SPEC-PROJ-V20-SCHED-v1：`docs/SPEC-PROJ-V20-SCHED-v1.md`，sha256 `a3af0b0dd1166870473910e11a27fe134951ccd1f64e9128ac8279acda9b7a38`
- SPEC-PROJ-V20-CICD-v1：`docs/SPEC-PROJ-V20-CICD-v1.md`，sha256 `5be226ba75c79c68933d13c07938b7e9b4f3635df391e2b4a5ae5371fbb1379b`
- SPEC-PROJ-V20-MOCK-v1：`docs/SPEC-PROJ-V20-MOCK-v1.md`，sha256 `1f8f42d237de876c5c552d84ea2f3d2a265d7ffcff59085a9a73023b00be0a45`
- 冻结入库：`409f055`（CICD v1）/ `5ddcaf8`（MOCK v1）/ Queue `8e12594`
- 施工期 Amendments：**无**（F3 数据源裁定以 Owner 追加裁定承载，未改 FROZEN Spec）

## 3. Baseline SHA
`04657d65bf30aed63ea481c950a4d16604389a97`（Build-B 合并后 main；Queue 声明的 NIGHT_BUILD_BASE）

## 4. Night Build Base SHA
`8e125948f08972d961c1354c05a97e812feeed22`（Step 0 完成后 docs 最新 commit「Build-C Queue 快照入库（SHA256 核验 MATCH）」）；
分支 `night-build/nb-20261007-C` 自此切出 —— `git merge-base ≤base> ≤branch>` 实测 = `8e12594`（一致）

## 5. Matrix Source SHA
`5873c66`（`docs(matrix): 需求追溯矩阵定稿（Owner 裁定 11 行全进）`，STATE 声明的 `matrix_source_sha`，为 base 祖先）；
当前 `docs/MATRIX.md` 工作树 sha256 = `d5b29bd6c662a1ea9c6711abbc01688a68af1d771609d280fc36f9194d62a63f`
本批消费行：#3 PROJ-V20-CICD、#6 PROJ-V20-MOCK（+ SCHED）

## 6. Queue Snapshot
`docs/night-build/QUEUE-SNAPSHOT-nb-20261007-C.md`（sha256 `c6270cb6e89aeb2d1e6e20a09ac26b2124fd08a219e59f41ce76548003111471`）：
- 冻结 3×READY（SCHED P0 / CICD P1 / MOCK P1）；NOT_READY 2（PROJ-V20-USER、DEBT-PRIVATE-API）+ BLOCKED 1（Android 真机）
- 施工序：SCHED → CICD → MOCK；迁移编号 0005→0006→0007（**冲突 = STOP**）
- 终态：**3 VERIFIED**；QUEUE 冻结后无新增/删除/改依赖

## 7. Environment
Scratch DB `autopilot_night_20261007c`；后端端口 **8004**；MySQL localhost:3306
生产隔离：`autopilot` / `autopilot_baseline` / 8000 **全程未触碰**；Smoke 用毕端口已释放
push 纪律：仅 Gitee 显式 URL（`https://gitee.com/Mr-6Lawrence/auto-pilot-test.git`），**禁 `git push origin`**
（origin 配置双 push URL：GitHub `ZipUp-dot/AutoPilot-Test` + Gitee，故本批一律显式 URL）

## 8. SUT
Scratch 项目：F1 `/projects/1`（调度）；F3 `smoke-mock` / `smoke-mock2`（target_url `http://example.com`，Web）

## 9. Feature Status
| # | feature_id | 终态 | 签字 | 说明 |
|---|---|---|---|---|
| 1 | PROJ-V20-SCHED | **VERIFIED** | Owner 2026-10-07 17:55 | FEATURE-ACCEPTANCE-C1-SCHED；AC-01~06 全 PASS，Kimi 独立核验通过 |
| 2 | PROJ-V20-CICD | **VERIFIED** | Owner 2026-10-07 18:15 | FEATURE-ACCEPTANCE-C2-CICD；AC-01~06 + 三红线 diff 核验 |
| 3 | PROJ-V20-MOCK | **VERIFIED** | Owner 2026-10-07 18:35 | FEATURE-ACCEPTANCE-C3-MOCK；AC-01~06 + Manifest 承载/单点注入/appium 零改动 |

> `终态` 列仅 Owner 可产出（手册 §十一）；三件验收记录均已入库（C2/C3 由 Owner 补放后入库，签字栏留空）。

## 10. Commit
| Feature | RED | feat | STATE 回填 / 终态 |
|---|---|---|---|
| F1 PROJ-V20-SCHED | 3 collection error + 11 failed | `7914488`（feat，迁移 0005） | `f65828b`（READY_CANDIDATE）→ `b7c8295`（VERIFIED）→ `a174721`（C1 入库） |
| F2 PROJ-V20-CICD | 14 failed + 1 collection error | `d867354`（feat，迁移 0006 + executions 加列） | `83f485a`（READY_CANDIDATE）→ `7bb248d`（三处置追认）→ `07f7909`（VERIFIED） |
| F3 PROJ-V20-MOCK | 15 failed + 3 collection error | `1155f59`（feat 20 文件，迁移 0007） | `627333c`（READY_CANDIDATE）→ `cac6deb`（VERIFIED + C2 入库）→ `0c99b82`（C3 入库） |
| 收口 | — | `ca7d7b9`（merge --no-ff，Build-C 三件套） | `2388f6c`（main 全量回归 + test_output.txt） |
| 环境/初始 | — | `7c5a531`（STATE 初始化）/ `912f0c8`（F1 开工：环境就绪 + E-3/E-5） | — |

## 11. Tests
- 后端全量（分支）：**1670 passed / 2 skipped / 0 failed**；coverage **TOTAL 88%**（9038 stmts / 888 miss / 2460 branches / 297 partial）
- Build-C 起点（Build-B 终态）：**1528 passed / 2 skipped** → 净增 **142 用例**（F1 +47、F2 +29、F3 +66）
- 前端：vitest **24 passed / 0 failed**；`vite build ✓`（F3 新增 `MockManage` chunk 已产出）
- 迁移证据（**双级 ×3**）：
  - 0005：SQLite `test_schedules_0005_upgrade_and_downgrade` + MySQL 实测升降
  - 0006：SQLite `test_pipelines_0006_upgrade_and_downgrade` + MySQL 实测升降
  - 0007：SQLite `test_mock_services_0007_upgrade_and_downgrade` + MySQL 实测升降
- main 复跑 + `backend/test_output.txt` 更新：合并后执行（`2388f6c`，**1670 passed / 2 skipped**，collected 1672）

## 12. E2E
NOT_TESTED（真实外部系统 E2E 不在本批范围；F1 调度 / F2 流水线 / F3 Mock 均以集成测试 + Smoke 四件套为正式证据；
`tests/integration/test_golden_path_real_chromium.py` 随全量 suite 通过，非本批新增证据）

## 13. Regression
零失败、既有断言零改动；唯一例外 = **E-1 迁移 head 漂移 parity 修正 ×3**（版本标记常量 + 紧随 delta 注释，非行为断言）：
`0004_ai_case_drafts→0005_schedules` → `0005_schedules→0006_pipelines` → `0006_pipelines→0007_mock_services`

## 14. Failures
| 时机 | 现象 | 处置 |
|---|---|---|
| F1 节拍 3 | `test_migration.py` head 漂移 1 failed | STOP → **E-1 parity** 授权改 2 行（0004→0005） |
| F1 节拍 1 | ASK 用户 spy `call_args.args` 下标错（未绑定记录） | 修正为 `args[1]/args[2]`（记录位序事实） |
| F2 开工 | §6 Seal 钩子 / §3 列清单 / §10「两表+索引」**三角冲突** | §八 STOP 上报 → Owner 裁定 **Option A**：`executions.pipeline_run_id`（INT nullable，FK RESTRICT + 索引） |
| F2 节拍 3 | MySQL **ERR 1553**：`Cannot drop index idx_exec_pipeline_run: needed in a foreign key constraint` | 改序「**先删 FK 约束 → 再删索引 → 最后删列**」（0004 同款警示延续，Owner 批准） |
| F2 节拍 3 | `test_migration.py` head 漂移 1 failed | **E-1 parity**（0005→0006） |
| F3 开工 | §5/§6/§7 `mock_server_id` 承载点 / §14 白名单 / P1-1 不变量**三角冲突** | §八 STOP 上报 → Owner 裁定 **Option A**：Manifest 承载（扩白名单 1 文件 `execution_admission_service.py`） |
| F3 节拍 2 | 未匹配分支原设计 `route.continue_()` 会跳过先注册的 SSRF handler | 改 `route.fallback()` 交回策略链；测试收紧（`fallback` 断言 + `continue_` 断言未调用） |
| F3 节拍 2 | AC-05 源码测试过严（docstring 提及 Appium 即 fail） | 收紧为禁引用实现（`appium_service` / `AppiumService` / `import appium`） |
| F3 节拍 3 | `test_migration.py` head 漂移 1 failed | **E-1 parity**（0006→0007） |
| 收口 | `git pull` 失败：origin fetch URL 为 GitHub，`Recv failure: Connection was reset` | 取回源改用 Gitee 显式 URL（`gitee/main` 与本地 main 同位 ⇒ Already up to date），并核对远端 SHA（Owner 2026-10-07 18:45 确认处理正确） |
| Smoke | PowerShell 内置只读变量 `$PID` 与项目 id 变量名冲突 → 404 误判 | 改名 `$projId` 重跑，全通过 |

### 14-附：Owner 裁定台账（3 次）
1. **F1 环境授权（2026-10-07 15:42）**：Scratch DB `autopilot_night_20261007c` / 端口 8004 / runtime 目录代执行授权；确认 E-3（Spec §14 路径笔误 → Schedule Schema 落 `app/models/schedule.py`）与 E-5（新增依赖 `croniter==6.2.4`）有效
2. **F2 三处处置追认（2026-10-07 18:05，无新增 STOP）**：① Option A 追认有效（`executions.pipeline_run_id` 性质 = 事实表追加式可空引用列，符合 7.6）；② Seal 钩子改用「`refresh_run_status` 唯一 writer + 读时重算」**批准**（不扩白名单、不新增状态出口，不变量 #7/#8 保持）；③ MySQL downgrade 顺序修复**批准**
3. **F3 数据源裁定 A（2026-10-07 18:20）**：`mock_server_id` **Manifest 承载**（来源 `project.config_json` → Admission 冻结 → 执行期只读 Manifest），白名单扩 1 文件

### 14-附：conflict 台账（3 条，全闭环）
验收记录签字时均**不在工作区**（Owner 宣称已放入 docs/night-build/，施工方实测缺失）→ 按 §四记 conflict、STATE 先行回填、签字栏不代填：
- C1-SCHED：Owner 17:56 补放 → `a174721` 入库；闭环
- C2-CICD：Owner 18:35 补放 → 随 `cac6deb` 入库；闭环
- C3-MOCK：Owner 18:36 补放 → `0c99b82` 入库；闭环

### 14-附：正面案例（Owner 2026-10-07 18:50 记入）
**不变量 #2 正确处置 —— 数字口径矛盾「原样采用 Owner 给值 + 即时上报」**：
手册 §二 累计口径初稿写「11 Feature VERIFIED」，与仓库实测台账不符
（Build-A **1** + Build-B **5** + Build-C **3** = **9 VERIFIED**；9 + 2 = 11 为**对象总数**）。
施工方按不变量 #2「测试数/覆盖率/KPI 只能引用 test_output.txt / STATE.json / Owner 给的值，
禁止估算或发明」→ **原样采用 Owner 给值、未擅改数字**，同时即时上报口径矛盾；
Owner 2026-10-07 18:50 裁定「11」系口误，修正为 **9 Feature VERIFIED + 2 撤项/延期**。

## 15. Blockers
无（Queue 内 BLOCKED 项「Android 真机」为下批范围，不阻塞本批 3 Feature）

## 16. Interruptions
无（未触发超时/中断恢复流程；QUEUE 冻结后无新增/删除/改依赖；迁移编号 0005→0006→0007 无冲突）

## 17. Not Ready
| feature_id | 状态 | 说明 |
|---|---|---|
| PROJ-V20-USER | NOT_READY | 设计未出 |
| DEBT-PRIVATE-API | NOT_READY | 设计未出 |
| Android 真机 | BLOCKED | 硬件未备（MATRIX `real_device_validated=PENDING`） |

## 18. Known Limitations
1. **F2 Seal 钩子未落地**：以「`refresh_run_status` 唯一 writer + 读时重算」实现同一派生语义（Owner 批准，不新增状态出口）；如需真 Seal 钩子须授权扩白名单（`execution_finalizer.py`）；
2. **F2 Spec §3 附录**：`executions.pipeline_run_id` 由 Owner 补档记载；补档将变更 SPEC-PROJ-V20-CICD-v1.md 内容 SHA，`spec_sha` 需 Owner 重新声明（本次核验时 SHA 仍为 `5be226ba…1379b`）；
3. **F3 Mock 为 HTTP-only**：不做 gRPC/TCP（v2 再议）；不做录制回放；命中次数统计不入库（日志派生，v2 入 metrics）；
4. **F3 规则规模**：单 server 规则数 <100 不优化；超限 STOP 上报（不变量 #2 数字可由 Owner 调整）；
5. **F3 拦截范围**：仅拦截 `base_path` 前缀；未匹配路径放行真实请求（`route.fallback()` 回既有 SSRF 链，不阻断）；
6. **GitHub 远端**不作本批要求（仅 Gitee 显式 URL 推送）；GitHub 欠推登记为本批欠账；
7. **main 合入方式**：由 Owner 授权直接 `merge --no-ff` + 显式 URL 推送（非 PR 流程），分支留痕保留；
8. **三次验收记录 conflict**（C1/C2/C3）均因「签字时记录文件未在工作区」引发，已由 Owner 补放全部闭环；纪律按 §十一（终态/签字仅 Owner 可产出、签字栏不代填）执行。

## 19. 总验收摘要（Owner 定稿 2026-10-07 18:45）

施工：SCHED / CI/CD / MOCK 三件全 VERIFIED（Owner 真实签字 17:55 / 18:15 / 18:35），
V2.0 四件套完成 3/4；USER 经 Owner 裁定 DEFERRED（盈利阶段再立项）。

测试：main 基线 1670 passed / 2 skipped / 0 failed（coverage 88%），
collected 1672；迁移 0005/0006/0007 均双级证据（SQLite 升降 + MySQL 实升降）。

治理：三次 Owner 裁定（环境授权/E-3/E-5 15:42；F2 三处置追认 18:05；F3 数据源
裁定 A 18:20）全部留痕；E-1×3 合规；conflict 台账×3 闭环；C-27 后无终态越权复发。

债务：GitHub 欠推（§18.6）；DEBT-PRIVATE-API 延期随手清；Android 真机 hardware
blocked；USER DEFERRED_BY_OWNER。

---

> **口径说明（施工方核对注，供 Kimi 定稿）**
> ① §9 `终态` 列与签字时间均以 Owner 消息为准；STATE 为唯一真源（`nb-20261007-C-STATE.json`）。
> ② §11「1528 → 1670」：1528 为 **Build-B 分支终态**（Build-C 起点），1670 为 **Build-C 分支终态**；main 复跑见 §11 末行。
> ③ §5 `matrix_source_sha` 为 STATE 声明的 **commit 锚**（`5873c66`），非文件 sha256；两者口径不同，已分列。
> ④ §10 F3 feat 为 **20 文件**（C3 验收记录正文写「21 文件」，属 Kimi 初验件笔误，不改核验件正文）。
> ⑤ §19「总验收摘要」已由 Owner 定稿补入（2026-10-07 18:45）。
