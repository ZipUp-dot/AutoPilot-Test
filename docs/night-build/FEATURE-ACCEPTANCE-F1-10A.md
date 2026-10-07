# FEATURE ACCEPTANCE RECORD · EXT-AITC-10A（AI TestCase 后端链）

> 机器初验产物（Evidence Index），最终签字权在 Owner · 2026-10-07
> 核验人：Kimi（独立 diff 审查，非施工方自述）

## 标识与血统
| 字段 | 值 |
|---|---|
| Feature | EXT-AITC-10A（Matrix #10 前半） |
| Spec | SPEC-EXT-AITC-10A-v1.1（FROZEN 11:05；Amendment v1.3/v1.4 增补） |
| BASE / 分支 | docs 最新 commit d1eefe1 → night-build/nb-20261007-B |
| Commit | 10afacd（RED 19 用例）→ 972977c（feat 15 文件）→ a2f159e（STATE 回填） |

## AC → 验证映射（独立核验结论）
| AC | 结果 | 独立证据 |
|---|---|---|
| AC-01 脱敏先于 Prompt（7 类） | **PASS** | test_evidence_sanitizer 4 用例 |
| AC-02 JSON Schema 校验（坏样本 invalid） | **PASS** | test_ai_case_service |
| AC-03 三维独立存储 | **PASS** | 模型 diff 三列独立（ai_assessment/validation_status/review_status） |
| AC-04 Promote=valid∧approved∧snapshot一致→TestCase(ai_draft) | **PASS** | test_ai_case_promote + Smoke DB 证据（test_cases.source=ai_draft） |
| AC-05 snapshot 复验不一致→needs_review 不自动拒绝 | **PASS** | service promote 逻辑核验 |
| AC-06 Excel 入口回归 | **PASS** | 1511 passed 基线（1492+19），case_service.create 默认 source='excel' |
| AC-07 Promote 零 Execution 副作用 | **PASS** | promote 体 diff 核验：仅创建 TestCase + 回填 promoted_case_id |
| AC-08 Draft 版本语义（旧 approval 失效） | **PASS** | test_ai_case_service |
| AC-09 source 不进 canonicalizer | **PASS** | 模型/服务 diff：hash 路径零改动 |

## 证据汇总
- Tests：1511 passed / 2 skipped / 0 failed（断言零改动；唯一例外 = E-1 head 漂移 parity 修正，Amendment v1.4 留痕）
- Schema：迁移 0004（evidence_snapshots + ai_case_drafts + element/test_case 增列）；alembic 级 upgrade+downgrade+MySQL 测试证据齐（硬边界增补 4 满足）
- 白名单：21 文件 diff 逐一对照 §14+E-1/E-3 扩展，零越界；_chat_http_attempt 复用核验通过（ai_service.generate_test_cases 走 _call_openai 链）
- Smoke 四件套：全 PASS（8003 + autopilot_night_20261007b；baseline/8000 未触碰）
- E2E 真实 LLM 链路：**NOT_TESTED**（一次非预期真实调用已披露并隔离，Mock 证据为准）

## Known Issues
1. Smoke 期间一次非预期真实 LLM 调用（PowerShell 丢弃空值 env 致回落真实 Key）；
   TRAE 主动披露、隔离重跑、正式证据基于 Mock——记为环境操作教训，下批 Smoke 前显式清空调用侧 env；
2. 真实链路端到端（含 DeepScope 实际生成质量）待下批批量生成时观察（转 F2/E2E 挂账）。

## 机器初验结论
**READY_CANDIDATE = YES（AC-01~09 全 PASS）——终局 VERIFIED 待 Owner 签字**

Owner 签字：____________________  日期：__________
