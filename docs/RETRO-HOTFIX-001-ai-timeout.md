# RETRO-HOTFIX-001 · AI 超时根治 追溯归档记录

> **性质声明（READ FIRST）**：本记录为 **追认归档（post-hoc ratification）**，不是按 Night Build 协议
> 走 Gate 流程产生的 Feature Acceptance Record。该修复施工于治理协议（Prompt Engineering v2.1）
> 全面生效之前，属于协议前紧急 hotfix。本文件的作用是把它纳入可追溯档案，**不伪造任何
> Gate PASS / Owner Freeze 时序**。Owner 于 2026-10-07 08:28 +08:00 决策采用方案 A（追认归档）。

---

## 1. 标识

| 字段 | 值 |
|---|---|
| RECORD_ID | RETRO-HOTFIX-001 |
| RECORD_TYPE | RETRO_ACTIVE_HOTFIX（追认，非协议内 Feature） |
| 关联 commit | `58382f8`（分支 `fix/ai-win-async-timeout`，GitHub + Gitee 已推送） |
| 施工时间 | 2026-10-07 00:07 +08:00（commit 时间戳） |
| Owner 追认 | APPROVED_BY=OWNER @ 2026-10-07 08:28 +08:00（会话决策，方案 A） |

## 2. SHA 关系（按 v2.1 第六节如实填写，缺项即缺项）

| 字段 | 值 | 说明 |
|---|---|---|
| FROZEN_SPEC_SOURCE_SHA | **N/A（DEVIATION-1）** | `docs/FROZEN_SPEC_V9.8.1.md` 从未入库，施工时规则来源为工作记忆 |
| FEATURE_SPEC_SOURCE_SHA | **N/A（DEVIATION-2）** | 无 Feature Spec / Freeze Manifest，属协议前施工 |
| MATRIX_SOURCE_SHA | **N/A** | 修复不经 Matrix 追溯（缺陷驱动，非 Proposal 驱动） |
| BASELINE_REF | tag `v9.8.1` → `8bfba07` | Baseline 锚点未被本修复移动 |
| BASE_BRANCH_AT_CONSTRUCTION | `f251085`（当时 main HEAD） | 修复分支基点 |
| COMMIT_SHA | `58382f8` | 本记录归档对象 |

**DEVIATION-1 处置**：Frozen Spec 引用规程要求四字段锁定，本次未满足。处置：记录为债，
Frozen Spec Bootstrap 完成前，任何 **新** 施工仍视为 Gate A 未通过。

**DEVIATION-2 处置**：无 14 节 Feature Design。补救：本记录 + 施工 Agent 的计划文档
`backend/.trae/documents/fix-ai-win-async-timeout.md`（本地保留，未入库）共同构成最小设计档案。

## 3. 缺陷与根因

**现象**：用例导入后批量生成中断；worker 线程永久挂起，前端显示"生成中断"。

**根因**：共享 AI 层 `ai_service._chat_http_attempt` 使用 `asyncio.wait_for` 做 deadline。
Windows Python 3.13 ProactorEventLoop 下，`wait_for` 超时后的 `task.cancel()` 无法中止
已发出的 overlapped socket recv，协程永久卡在 `await recv_future`，占满
`ThreadPoolExecutor(2)`，批量生成饿死。

**影响面**：三条链路共用 `_chat_http_attempt`——批量/单条生成、vision、heal。

## 4. 修复内容（diff 核验过）

- `_chat_http_attempt`：`asyncio.wait_for` → `asyncio.wait(FIRST_COMPLETED)` +
  `loop.call_later` 墙钟看门狗 + 到点主动 `client.aclose()` 真正中止底层 socket I/O；
  从不 await 被取消的 post_task，杜绝僵尸任务。
- 签名、`error_type` 七值闭集、quota/slot 语义、三条调用方 **零改动**。
- 中途一次测试耦合（watchdog sleep 干扰限流器 backoff 观测）通过改用 `loop.call_later`
  解决，未修改被测契约。
- 新增 `backend/tests/unit/test_ai_timeout_no_hang.py`（4 用例）。

## 5. 验证证据（均为实测，来源：施工记录）

| 类型 | 证据 |
|---|---|
| 真实 socket 冒烟 | 无限 chunked 流 + deadline=1s → **1.26s** 真实终止并抛 `DeadlineExceeded`（修复前同场景卡死 13+ 分钟） |
| 单条生成 E2E | case 211：HTTP 200，63.9s 返回 1431 字符代码（旧代码 25s+ 无响应） |
| 批量 E2E | 干净 batch `a2bd188b` 3/3 success（100s）；最大规模 batch `e14ad39f` **57/57 success，0 failed，0 skipped**（18 分钟，120/120 用例全部生成完毕） |
| 恢复性验证 | 重启后 6 个遗留卡死 batch 全部由修复后代码跑完（20/20 ×3、40/40、20/20、33+） |
| 单元/回归 | deadline 测试 7/7；全量回归 **1489 passed / 2 skipped / 0 failed**（1485 + 4 新用例，无下滑） |

**AC → 验证映射**：
- AC-01 取消免疫的 post 在 deadline 到点必须终止且不挂起 → `test_ai_timeout_no_hang.py`（4 用例 PASS）+ 真实 socket 冒烟 1.26s。
- AC-02 修复不得改变 quota/slot/backoff 契约 → 既有 deadline/限流器测试全绿（未改断言）。
- AC-03 三条调用链（生成/vision/heal）行为不变 → 调用方零改动 + 全量回归无失败。
- AC-04 端到端无卡死 → batch `e14ad39f` 57/57。

## 6. Schema 影响

无 Schema 变更（未触及 Alembic，符合 Schema Authority 不变量）。

## 7. 已知残余风险（未处理，转后续独立 Feature）

- `acquire_slot` 使用 `threading.BoundedSemaphore` 同步阻塞于 async 函数内，会阻塞
  worker 线程的 event loop（非本次卡死根因，但属同类隐患）。建议后续独立分支改
  `asyncio.Semaphore` 或 offload 到线程池——**该后续施工必须走完整 Night Build 协议**。
- `test_output.txt` 在施工工作树中呈删除状态（历史遗留，非本次改动，未带入 commit）。

## 8. 归档清单

| 材料 | 位置 |
|---|---|
| 本记录 | `docs/RETRO-HOTFIX-001-ai-timeout.md`（随归档 commit 入库） |
| 修复 commit | `58382f8`（已推送双远程） |
| 施工计划文档 | `backend/.trae/documents/fix-ai-win-async-timeout.md`（本地，建议下次入库） |

---

*本记录由 Kimi 依据施工会话记录与 git diff 核验生成；数字均来自施工记录中的实测输出，未二次发明。*
