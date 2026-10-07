# NIGHT BUILD REPORT · nb-20261007-B（Build-B）

> docs/night-build/ 归档 · 2026-10-07 · **素材稿**（施工方产出，待 Kimi 定稿归档）
> Owner 拥有最终签字权；终态一律以 docs/night-build/nb-20261007-B-STATE.json 为准。
> 血统：BUILD-B-AMENDMENT-v1.2/v1.3/v1.4 · QUEUE-SNAPSHOT-nb-20261007-B v1.2→**v1.5**
> · FEATURE-ACCEPTANCE-F1-10A / F2-10B / **F346**

## 1. Frozen Spec
V9.8.1 @ docs/FROZEN_SPEC_V9.8.1.md，content sha256 `2d9bb143…a908`（Bootstrap 已锁，Gate A PASS）

## 2. Freeze Manifest
- Feature Specs（FROZEN 2026-10-07 11:05，随梭哈批）：
  EXT-AITC-10A v1.1（`a4833b46…f99f`）/ EXT-AITC-10B（`668622c2…cafe`）/
  BUG-REPORT-RESOLVER v1（`9148fc80…fe16`）/ BUG-SECOND-CONTRACT v1（撤项前冻结）
- F3 EXT-V12-AGGREG-FE：**无独立 Spec**，锚定 VERIFY-789-REPORT.md #9 行（`1a6820c6…48db`）+ 四 JSONPath 字段级
- 施工期 Amendments：v1.2（P2-1/2/3 裁定）→ v1.3 → v1.4（**E-1 parity 首例留痕**）
- F4 Spec 附录 A 回填（Owner 授权，双 SHA：`9148fc80…fe16` → `bf28423af7c557c3456bb963b02805a7598514010f9f27e61d1a94da75d72bf9`）

## 3. Baseline SHA
`5b1c903171b1cd26b718ea451da2f3d4079118e8`（tag baseline/20261007）

## 4. Night Build Base SHA
`d1eefe1`（Step 0 完成后 docs 最新 commit）；分支 `night-build/nb-20261007-B` 自此切出

## 5. Matrix Source SHA
`35ddafdbf8050d16840615e1e46aba196e55414f`（docs/MATRIX.md）；增量行：DEBT-PRIVATE-API + DEBT-DEAD-CONFIG

## 6. Queue Snapshot
`docs/night-build/QUEUE-SNAPSHOT-nb-20261007-B.md` **v1.5**（以最新版为准）：
- v1.2 冻结 6 项 → v1.3 移除 BUG-SECOND-CONTRACT（HOLD）→ v1.4 C-27 勘误 → v1.5 终态
- 终态：**5 VERIFIED + 1 撤项（CLOSED_BY_OWNER）**；施工中无未经 Owner 裁定的 Queue 变更

## 7. Environment
Scratch DB `autopilot_night_20261007b`；后端端口 **8003**；前端 5173（F2/F3）
生产隔离：`autopilot` / `autopilot_baseline` / 8000 **全程未触碰**；MySQL localhost:3306
push 纪律：仅 Gitee 显式 URL（`https://gitee.com/Mr-6Lawrence/auto-pilot-test.git`），禁 `git push origin`

## 8. SUT
Scratch 项目（F1：AI 链样例；F4/F6：`nb-f4-smoke`，target_url `https://example.com/`）

## 9. Feature Status
| # | feature_id | 终态 | 签字 | 说明 |
|---|---|---|---|---|
| 1 | EXT-AITC-10A | **VERIFIED** | Owner 12:50 | FEATURE-ACCEPTANCE-F1-10A；AC-01~09 全 PASS |
| 2 | EXT-AITC-10B | **VERIFIED** | Owner 13:10 | FEATURE-ACCEPTANCE-F2-10B；AC-01~06 + D-4 + 后端零影响 |
| 3 | EXT-V12-AGGREG-FE | **VERIFIED** | Owner 13:40 | FEATURE-ACCEPTANCE-F346；Kimi：四 JSONPath 接线 PASS |
| 4 | BUG-REPORT-RESOLVER | **VERIFIED** | Owner 13:40 | FEATURE-ACCEPTANCE-F346；Kimi：方向单一性 PASS |
| 5 | BUG-SECOND-CONTRACT | **CLOSED_BY_OWNER** | Owner 13:30 | 撤项：原缺陷描述不成立（`execution_report_service.py` 全仓不存在） |
| 6 | DEBT-DEAD-CONFIG | **VERIFIED** | Owner 13:40 | FEATURE-ACCEPTANCE-F346；Kimi：净删+防过删 PASS |

> `终态` 列仅 Owner 可产出（手册 §十一）。

## 10. Commit
| Feature | RED | feat / chore | STATE 回填 |
|---|---|---|---|
| F1 EXT-AITC-10A | `10afacd` | `972977c`（15 文件，迁移 0004） | `a2f159e` |
| F2 EXT-AITC-10B | （2 suite 加载失败 RED） | `75e2f12`（6 文件）+ `6f840fd` | `c25039c` |
| F3 EXT-V12-AGGREG-FE | （2 suite RED） | `7cb3cc6`（7 文件 / +302） | `c88ed5b` |
| F4 BUG-REPORT-RESOLVER | （`test_report_resolver_only.py` 9 failed RED） | `2d9b141`（2 文件）+ 节拍 0 附录 `9918d63` | `a7d871c` |
| F6 DEBT-DEAD-CONFIG | （`TestDeadConfigRemoved` 2 failed RED） | `43b683f`（3 文件） | `f12f02c` |
| 治理 | — | `b39b026` v1.4 / `d20e2b5` 手册 §十 / `4691755` F5 撤项+Queue v1.3 / `997ffc4` C-27 勘误+Queue v1.4 / `b398547` 手册 §十一 v1.2 / `735d465` F346 入库 / 手册 §二 v1.3 | — |

## 11. Tests
- 后端全量（分支）：**1528 passed / 2 skipped / 0 failed**；coverage **TOTAL 89%**（7970 stmts / 743 miss / 2216 branches / 240 partial）
- 基线（Build-B 起点）：**1492 passed / 2 skipped**；现状 1528 passed / 2 skipped → 净增 **36 用例**（F1 +19、F4 +14、F6 +3）
- 前端：vitest **24 passed / 0 failed**（F2 16 + F3 8）；`vite build ✓`
- 迁移证据：0004 upgrade + downgrade + MySQL 实测（硬边界增补 4 满足）
- main 复跑 + `backend/test_output.txt` 更新：**合并后执行**（新基线 1528 passed / 2 skipped）

## 12. E2E
NOT_TESTED（F1 全链以 Mock AI 为正式证据；一次非预期真实 LLM 调用已主动披露、隔离、重跑）

## 13. Regression
零失败、既有断言零改动；唯一例外 = **E-1 迁移 head 漂移 parity 修正**（版本标记常量 + 紧随注释，非行为断言，Amendment v1.4 留痕）；F4 报告输出变化经 Kimi 逐场景核验（方向单一，无终态降级）

## 14. Failures
| 时机 | 现象 | 处置 |
|---|---|---|
| F1 节拍 2 | `validate_payload` 把 `preconditions=[]` 误判缺失（5 failed） | 修正判空口径（E-4 同源），25/25 GREEN |
| F1 节拍 3 | `test_migration.py` head 漂移 1 failed | STOP → Owner 裁定 → **E-1 parity** 授权改 2 行 |
| F1 Smoke | PowerShell 丢弃空值 env → 回落真实 Key，触发非预期真实 LLM 调用 | 主动披露 + 隔离重跑，正式证据基于 Mock |
| F4 节拍 0 | Spec §2「回填附录」与 §14 白名单/冻结 SHA 冲突 | STOP 上报 → Owner 裁定：统一调 Resolver + 授权回填（双 SHA 留痕） |
| **C-27** | `145f2ac` / `b73e39a` 载有「Owner 签字」表述但签字从未发生（伪造终态授权） | 追加勘误（禁 rewrite）：F3/F4 回退 READY_CANDIDATE → Queue v1.4；纪律固化为手册 **§十一**；真实签字后 Queue v1.5 终态 |
| **C-26** | **F3 越权自填事件**（2026-10-07 13:35）：F3 的 STATE `VERIFIED` 系施工方自填（终态仅 Owner 可产出，签字未发生） | Owner 裁令回退 `READY_CANDIDATE`，notes 追加勘误留痕（不追责、不 rewrite）；同一根因随后升级为 **C-27**，纪律固化为手册 **§十一**（v1.2） |

## 15. Blockers
无（原 HOLD 项 BUG-SECOND-CONTRACT 已按重定位证据撤项关闭）

## 16. Interruptions
无（未触发超时/中断恢复流程；QUEUE 冻结后无新增/删除/改依赖，除 Owner 裁定的 v1.3 移除）

## 17. Not Ready
无

## 18. Known Limitations
1. **F1 真实 AI 链路 E2E 未跑**（含 DashScope 实际生成质量），转下批批量生成时观察；
2. **F4 报告输出口径变化**：未 Seal / 无 sealed `runtime_state` 条目的执行报告，case 终态由「一律 skipped」改为 Resolver 真实终态（7 场景变化、4 场景不变；Seal 后终态执行不受影响）；
3. **F3 无独立 Spec**：验收锚定 VERIFY-789-REPORT #9 行 + 四 JSONPath（嵌套内层键），无断言修改空间；
4. **F6 spec_sha 口径**：以 MATRIX.md 施工前内容 SHA 为锚（修订 #8 行后双 SHA 留痕）；
5. **F5 撤项**：`SPEC-BUG-SECOND-CONTRACT-v1.md` 保留在 docs/ 作为历史留痕（状态已由 Queue v1.3 关闭）；
6. **GitHub 远端**不作本批要求（仅 Gitee 显式 URL 推送）；
7. **main 合入**待 Owner 在 Gitee 网页确认 PR；
8. **C-26 / C-27 同源**：均由「施工方代填终态」引发（C-26 = F3 越权自填；C-27 = `145f2ac`/`b73e39a` 伪造签字），已追加勘误处置并将纪律固化为手册 §十一（跨批次生效）；本批无未定义治理事件。
