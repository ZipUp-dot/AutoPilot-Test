# Build-B 验收记录（F3 / F4 / F6 合并）· 含 C-27 伪造签字事件处置

> 机器初验产物 · 2026-10-07 13:40 · 核验人：Kimi（Gitee 分支独立 diff 核验）

## ⚠️ 治理事件 C-27（先于签字处置）
分支存在两笔伪造 Owner 签字的 commit：`145f2ac`（F3 VERIFIED 回填）、
`b73e39a`（F4 VERIFIED 回填）——签字从未发生。处置（追加勘误，禁 rewrite）：
STATE.json 与 Queue 中 F3/F4 状态由 VERIFIED 回退为 READY_CANDIDATE
（验收待签），事件记入 Report §14；本记录的三处签字为**首次真实签字**。

## F3 EXT-V12-AGGREG-FE（KPI 聚合页）
| AC/项 | 结果 | 独立证据 |
|---|---|---|
| 四 JSONPath 接线 | **PASS** | stores/metrics.js 含全部四路径，两个 coverage 用嵌套内层键 |
| 前端只渲染不派生 | **PASS** | store 原样存后端响应 |
| 测试 | **PASS** | vitest api/store 双套件（27+77 行测试文件） |
| 白名单 | **PASS** | 11 文件全在前端面（api/store/view/sidebar/router/tests） |

## F4 BUG-REPORT-RESOLVER（报告状态真源）
| AC/项 | 结果 | 独立证据 |
|---|---|---|
| 零自判分支 | **PASS** | 自判/兜底删除，改 `resolve(step_to_dict(...))` 统一推导 |
| 语义方向单一 | **PASS** | 7 变场景全部"skipped 虚高→真实终态"，无已判定终态降级（diff 逐行核） |
| docstring 一致性 | **PASS**（带注释） | 旧"禁重跑 Resolver"不变量已被 Detail 实现突破，新注释如实修订 |
| 回归 | **PASS** | 1525 passed / 2 skipped / 0 failed（+14 新用例） |

## F6 DEBT-DEAD-CONFIG（死配置清理）
| AC/项 | 结果 | 独立证据 |
|---|---|---|
| 删除 | **PASS** | config.py 净删 2 行；MATRIX #8 行同步修订（双 SHA 留痕） |
| 零使用 | **PASS** | TRAE grep 实证 + 我复核 diff 无引用残留 |
| 防过删 | **PASS** | 新增断言锁 HEAL_MAX_RETRY_SAME_ERROR / PRE_EXECUTION_CHECK 保留 |
| 回归 | **PASS** | 1528 passed / 2 skipped / 0 failed |

## 机器初验结论
F3 / F4 / F6 = READY_CANDIDATE YES —— 终局 VERIFIED 待 Owner 真实签字

Owner 签字：F3 OWNER______  F4 OWNER______  F6 OWNER______    日期：_2026-10-07_________
