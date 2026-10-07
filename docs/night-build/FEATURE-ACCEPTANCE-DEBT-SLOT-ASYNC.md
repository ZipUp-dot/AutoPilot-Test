# FEATURE ACCEPTANCE RECORD · DEBT-SLOT-ASYNC

> 机器初验产出（Evidence Index），**最终签字权在 Owner**。2026-10-07

## 标识与血统

| 字段 | 值 |
|---|---|
| Feature | DEBT-SLOT-ASYNC（slot 获取异步化） |
| requirement_source | TechDebt（RETRO-HOTFIX-001 §7 残余风险） |
| Spec | DEBT-SLOT-ASYNC-v1.1（FROZEN，Owner 09:41 冻结 + 10:05 增补追认，方案 B） |
| BASE_SHA | 5b1c903171b1cd26b718ea451da2f3d4079118e8（== NIGHT_BUILD_BASE_SHA） |
| 分支 | night-build/nb-20261007（自 5b1c903 切出，已推 Gitee） |
| Commit | `08418a22c6d9fcc9dbefdb28801c9965c8fe554c`（feat）· `3d4c7fa`（RED 测试先行） |
| Schema | **零变更**（diff 核验：无 alembic/模型触碰） |

## AC → 验证映射（AC 只允许 PASS / FAIL / BLOCKED / NOT_TESTED）

| AC | 验证方法 | 结果 | 证据 |
|---|---|---|---|
| AC-01 等待期 loop 可调度 | test_wait_does_not_block_loop | **PASS** | 节拍2：3 passed/0.66s |
| AC-02 计数语义不变（1 Attempt=1 Quota=1 Slot；backoff 不占 slot） | 既有 limiter 测试全量 GREEN、断言零修改 | **PASS** | 全量 1492 passed 中含全部既有 limiter 用例 |
| AC-03 取消后无槽泄漏 | test_cancelled_wait_releases_late_acquire | **PASS** | 节拍2：active_count 回基线 |
| AC-04 三链兼容、零断言改动 | 全量回归 + diff 独立核验 | **PASS** | 1492 passed / 2 skipped / 0 failed；`_LimiterSpy` 仅 +4 行委托（裁定 A） |
| AC-05 等待受 remaining 约束 | test_wait_respects_deadline | **PASS** | 节拍2：slot_timeout 墙钟 ≈ remaining |

## 证据汇总

- **Tests**：1492 passed / 2 skipped / 0 failed（基线 1489 + 新增 3，零下滑）；
  覆盖率 TOTAL 7632 / 652 / 2152 / 226 / 90%（基线 7605 / 651 / 2144 / 225，全面微升）
- **Regression**：见上；既有断言文本零改动（diff 独立核验，非施工方自述）
- **Smoke 四件套**：Service Startup ✅ /health → healthy；Core Route ✅ GET /api/v1/projects；
  Module Entry ✅ openapi 24 paths 全注册；Happy Path ✅ POST+GET 项目 + MySQL 直连复核落库
- **隔离合规**：8002 + autopilot_night_20261007；8000 生产（PID 8604）与 autopilot_baseline 未触碰；
  SSRF 拦截实测有效（8002 不在 allowlist → 422）
- **E2E（真实 AI 链路）**：**NOT_TESTED** — 本 Feature 为调度层行为变更，单元+Smoke 已覆盖验收面；
  真实 LLM 链路建议下一次批量生成时顺带观察（转 Known Limitation，见下）
- **RED 先行**：3d4c7fa 提交时 3 failed（AttributeError=新 API 不存在），形态合规

## 越界检查

禁改清单（_chat_http_attempt / retry-backoff / _call_heal_ai / active_count / 单例工厂）diff 核验全部未触碰；
改动 5 文件均在 Spec §14 白名单（含裁定 A 追认的第 5 文件）。

## Known Issues / Limitations

1. **真实 AI 链路 E2E 未跑**（NOT_TESTED）：下轮批量生成（走 Scratch 环境）时观察 slot 等待日志即可闭环，不阻塞本 Feature 验收；
2. `run_in_executor` 用默认线程池（D-3 已裁定）：等待线程最长挂起 = remaining，无饥饿风险；
3. manifest 的 spec_source_sha 指向 v1.1 增补 commit（在 main 上），分支内未含治理文档——治理文档与施工代码分离，符合"每 Feature 一个 commit"的边界。

## 机器初验结论

**Feature Status 建议：VERIFIED**（E2E 项以 Known Limitation 挂账，非 BLOCKED）

Owner 签字：____________________  日期：__________
