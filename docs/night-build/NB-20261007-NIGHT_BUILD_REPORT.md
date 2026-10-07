# NIGHT BUILD REPORT · nb-20261007

> docs/night-build/ 归档 · 2026-10-07 · 机器初验产物，Owner 拥有最终签字权

## 1. Frozen Spec
V9.8.1 @ docs/FROZEN_SPEC_V9.8.1.md，content sha256 2d9bb143…a908（Bootstrap 已锁，Gate A PASS）

## 2. Freeze Manifest
DEBT-SLOT-ASYNC：spec v1.1，content sha256 8e337565…6c5f；approved_by=OWNER @ 09:41（+10:05 增补）；manifest 已入库（main）

## 3. Baseline SHA
5b1c903171b1cd26b718ea451da2f3d4079118e8（tag baseline/20261007，Gate B PASS）

## 4. Night Build Base SHA
== Baseline（5b1c903），分支 night-build/nb-20261007 自此切出（Gate C PASS，不变量 #13）

## 5. Matrix Source SHA
5873c664b278f4361e52e12b11eeb35dfdb01b6e（docs/MATRIX.md，11 行全进 @ 09:36 裁定）

## 6. Queue Snapshot
docs/night-build/QUEUE-SNAPSHOT-nb-20261007.md：唯一 READY = DEBT-SLOT-ASYNC（P0，无依赖）；施工中无新增/删除/改依赖（不变量 #14、#15 合规）

## 7. Environment
Scratch DB autopilot_night_20261007（autopilot / autopilot_baseline 未触碰）；端口 8002；runtime C:\night_build_runtime\nb-20261007；8000 生产（PID 8604）隔离

## 8. SUT
Smoke 用项目 nb-smoke-20261007（写入 Scratch DB，HTTP 入口走 https://example.com/ 通过 SSRF 校验）

## 9. Feature Status
| feature_id | runtime | final | 说明 |
|---|---|---|---|
| DEBT-SLOT-ASYNC | COMPLETED | **VERIFIED（建议，待 Owner 签字）** | 1 次增补（v1.1），0 次 INTERRUPTION |

## 10. Commit
3d4c7fa test(RED) → 08418a2 feat（+53 limiter / 3 处一行调用 / +4 替身委托）；分支已推 Gitee

## 11. Tests
1492 passed / 2 skipped / 0 failed；coverage 7632/652/2152/226/90%（基线全量 1489 + 新增 3）

## 12. E2E
NOT_TESTED（调度层 Feature；真实 AI 链路转 Known Limitation，见 §18）

## 13. Regression
零失败、既有断言零改动；SSRF 防护链路实测拦截有效（正向证据）

## 14. Failures
施工中 1 次：节拍 3 全量回归 13 failed（_LimiterSpy 接口缺口）→ 硬边界 STOP 上达 Owner → 裁定 A + Spec v1.1 增补 → 修复闭环。无静默失败。

## 15. Blockers
无

## 16. Interruptions
无（未触发超时/中断恢复流程）

## 17. Not Ready
无（Queue 内仅此 1 Feature）

## 18. Known Limitations
1. 真实 AI 链路 E2E 未跑，下轮批量生成时顺带观察 slot 等待日志；
2. 默认线程池承载等待（D-3 裁定），最长挂起=remaining；
3. GitHub 远端落后（网络不可达，恢复后补推 main 与 night-build 分支）；
4. main 合入决策待 Owner 签字后执行（Gitee 已可开 PR night-build/nb-20261007 → main）。
