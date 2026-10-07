# FEATURE-SPEC · DEBT-SLOT-ASYNC · slot 获取异步化

> 状态：**FROZEN**（Owner 决策 2026-10-07 09:41，方案 B） · 2026-10-07
> 层级：Feature Design（v2.1 第四节，强制 14 节）
> 输入：Frozen Spec V9.8.1（hash 已锁）· Matrix（DEBT-SLOT-ASYNC 行，Owner 已裁定全进）
> · 代码快照 BASE `5b1c903`

## 1. Feature Definition

**范围**：AI 限流器并发槽（slot）的获取等待从"同步阻塞 event loop"改为"异步等待"，
event loop 在排队等槽期间保持可调度。涉及 3 个 async 调用点 + 限流器本体。

**非目标（non-goals）**：
- 不改变 quota/slot/attempt 的任何计数语义（Spec §13 结构原样保留）；
- 不合并/拆分限流器单例，不改变 `AI_MAX_CONCURRENCY` / `AI_RATE_LIMIT` 配置口径；
- 不动 heal_service 的同步 `_call_heal_ai`（经审计：该路径仅在 Mock 模式被调用，
  不发起真实 AI 请求，同步阻塞无害）；
- 不引入 Redis/分布式信号量（原则 2：不引入额外基础设施）。

## 2. Existing Code Audit

**复用**：
- `app/utils/ai_rate_limiter.py` — `AIRateLimiter` 全部保留：滑动窗口 quota（`_lock`
  保护，纯内存快路径）、`threading.BoundedSemaphore` 作为**跨线程唯一事实源**、
  `acquire_attempt`/`release_slot`/`_rollback_quota` 语义。理由：限流器是进程内单例，
  被多个 worker 线程各自的 event loop 共享（每个 `asyncio.run` 一个 loop +
  orchestrator 主 loop + vision），`asyncio.Semaphore` 不能跨 loop 共享，必须保留
  threading 信号量。
- 三处调用方的 `finally: release_slot()` 模式（ai_service.py:682、:755；
  heal_service.py:1725）——**原样保留**，这是"backoff 不占 slot"的既有实现。

**不复用/需改造**：
- `acquire_attempt()` 内的 `self._semaphore.acquire(timeout=remaining)`（ai_rate_limiter.py）
  —— 在 async 函数内同步阻塞 event loop，是本次唯一要改的行为。排期等待期间该 loop
  上其他任务（如同 loop 内 in-flight 请求的 deadline 看门狗）被延迟调度。

**理由**：RETRO-HOTFIX-001 已根治"取消无法中止 I/O"；本债务是其记录的残余风险
（slot 等待阻塞 loop，同族隐患，非当时卡死根因）。

## 3. Data Model

无变更。限流器状态（`_calls` 窗口、信号量计数）均为进程内易失内存态，非持久化事实，
不进 9 事实表体系。`no_schema_change`。

## 4. State Model

不引入新状态机。slot 的生命周期维持 Spec §13：

```
acquire_attempt_async → 持有 → HTTP attempt → finally release_slot
```

- writer：`AIRateLimiter`（唯一）；reader：`active_count` 监控属性（既有）。
- 无持久状态、无终态、无恢复语义。不变量 #9 保持：**1 HTTP Attempt = 1 Quota = 1 Slot**。

## 5. Data Flow

```
async 调用方（生成/vision/heal 三链）
  → acquire_attempt_async(remaining)
      → acquire()            （quota，同步快路径，_lock，非阻塞 —— 不动）
      → _acquire_slot_async  （executor 内 semaphore.acquire(timeout=remaining)
                              loop 全程可调度；超时/取消后迟到获取立即自释放）
      ← 失败回滚 quota（_rollback_quota，不动）
  → _chat_http_attempt（不动）
  → finally release_slot()   （不动）
```

## 6. Backend

**`app/utils/ai_rate_limiter.py`（唯一结构改动点）**：

新增方法（既有方法全部保留不动）：

```python
async def acquire_slot_async(self, timeout: Optional[float] = None) -> bool:
    """异步等槽：loop 不被阻塞；超时或取消后若迟到获取成功则立即自释放，杜绝泄漏。"""

async def acquire_attempt_async(self, remaining: Optional[float] = None) -> Optional[str]:
    """acquire_attempt 的异步版：quota 同步 + slot 异步等待 + 失败回滚。
    返回语义与同步版完全一致（None=成功 | "quota_timeout" | "slot_timeout"）。"""
```

实现要点：
- `_acquire_slot_async` 用 `loop.run_in_executor(None, ...)` 把 `semaphore.acquire(timeout=...)`
  放入默认线程池；`asyncio.Event` 标记取消，迟到获取分支 `release()` 后返回 False；
- executor 内的阻塞带 `timeout=remaining`，超时即返回，无线程无限挂起；
- `acquire()`（quota）保持同步：`_lock` 保护的微秒级快路径，阻塞 event loop 的时间
  可忽略，不值得异步化（避免过度设计）。

**调用方改动（3 处，仅一行级替换）**：
- `ai_service.py:664` `_call_openai_async`：`reservation = await ai_rate_limiter.acquire_attempt_async(remaining)`
- `ai_service.py:744` `_call_openai_vision_async`：同上
- `heal_service.py:1699` `_ai_attempt`：同上

**禁改清单**：`_chat_http_attempt`（刚根治的 watchdog 逻辑）、retry/backoff 链、
`_call_heal_ai` 同步路径、`active_count` 属性、单例工厂。

## 7. Frontend

无变更。slot 属后端执行治理，前端无展示状态（Spec §51 前端契约不涉及）。

## 8. Execution / Heal / Report / Metrics Integration

- 三链共用同一入口点（acquire_attempt_async），**无第二 Execution Contract、无第二
  状态体系**；限流器仍是进程内单例（README 单 worker 约束的前提不变）；
- Heal Round 的 deadline 语义不变：slot 等待上限仍由 `remaining` 约束；
- Report/Metrics 不读取 slot 内部态，零影响。

## 9. KPI Impact

- Original KPI（≥70% / ≥85% / ≤60s / ≥100 行）：**零影响**——改动不改变任何 AI 调用
  的成功率/时延口径，仅消除等待期的 loop 阻塞。
- Engineering Metrics（独立列）：slot 等待期间 event loop 阻塞时长 → 0（验收用
  标记协程法实测）。

## 10. Schema Delta

`no_schema_change`。无表/列/索引/FK/backfill。不触碰 `alembic/`、
`legacy_baseline.py`（Schema Authority 不变量）。

## 11. Acceptance Criteria

| AC | 内容 | acceptance_method |
|---|---|---|
| AC-01 | slot 等待期间 event loop 不被阻塞：槽满时触发等待，同 loop 内标记协程须在 ≤0.1s 内被调度 | 新增单测：占满并发槽 → 并发触发 acquire_slot_async → 断言标记协程执行完成且总耗时不低于槽释放时刻 |
| AC-02 | 计数语义不变：1 Attempt = 1 Quota = 1 Slot；backoff 不占 slot | 既有 `test_limiter_semantics` 等全部 GREEN、断言零修改 |
| AC-03 | 超时/取消后无槽泄漏：取消路径下迟到获取必须自释放，`active_count` 归零 | 新增单测：取消 wait 后强制槽可用，断言 active_count 回到基线 |
| AC-04 | 三调用方（生成/vision/heal）行为兼容：error_type 闭集、retryable 语义、release 时机全部不变 | 既有三链单测全量 GREEN + 全量回归无下滑 |
| AC-05 | deadline 约束保持：slot 等待上限 = remaining，超时返回 slot_timeout | 新增单测：remaining=0.2s 且槽满 → 返回 slot_timeout 且墙钟 ≈0.2s |

## 12. Acceptance Tests（AC → Test 映射）

| AC | 测试 | 预期 |
|---|---|---|
| AC-01 | `tests/unit/test_slot_async.py::test_wait_does_not_block_loop` | 新行为，先 RED 后 GREEN |
| AC-02 | 既有 `tests/unit/test_ai_rate_limiter*.py` / `test_limiter_semantics` | 回归 GREEN（不改断言） |
| AC-03 | `test_slot_async.py::test_cancelled_wait_releases_late_acquire` | 新行为，RED → GREEN |
| AC-04 | 既有 ai_service / heal_service 单测 + `tests/services/` 相关套件 | 回归 GREEN |
| AC-05 | `test_slot_async.py::test_wait_respects_deadline` | 新行为，RED → GREEN |

新测试文件 1 个：`backend/tests/unit/test_slot_async.py`。执行顺序符合固定节拍：
**先写测试（RED）→ 实现 → GREEN**。

## 13. Risk Audit（逐项引用 Frozen Spec Rule ID）

| 风险 | 对策 | Spec / 不变量依据 |
|---|---|---|
| 迟到获取导致槽泄漏 | 取消标记 + 迟到分支自释放（AC-03 锁定） | 不变量 #9 |
| loop 间共享信号量被误改 asyncio.Semaphore | 显式禁止；跨 loop 必须用 threading 原语 | 代码事实（多 loop 共享单例） |
| quota 语义漂移 | quota 路径零改动；失败回滚复用 `_rollback_quota` | §13 AI Limiter 结构 |
| retry/backoff 语义漂移 | 调用方 finally 模式不动；attempt 链不动 | §14 AI Retry；§13「backoff 不占 slot」 |
| 线程池饥饿（executor 默认池打满） | 阻塞带 timeout，最长挂起 = remaining（默认 ≤300s 配置值，实际为 case 预算）；executor 仅用于等待不占计算 | 工程经验（非冻结规则） |
| 测试与实现耦合（参照 RETRO 教训：mock asyncio.sleep 被内部 sleep 干扰） | 新实现不经 asyncio.sleep；新测试不 patch 全局 sleep | RETRO-HOTFIX-001 §4 教训 |
| 改动越界触碰 Frozen Architecture | 文件白名单 4 个（见 §14），禁改清单 §6 | 不变量 #20 / §7.6 |

## 14. File Modification Scope

| 顺序 | 文件 | 改动 | 依赖 |
|---|---|---|---|
| 1 | `backend/tests/unit/test_slot_async.py` | 新增（AC-01/03/05，先 RED） | 无 |
| 2 | `backend/app/utils/ai_rate_limiter.py` | 新增 `acquire_slot_async` / `acquire_attempt_async`（既有方法不动） | 1 |
| 3 | `backend/app/services/ai_service.py` | 2 处调用改 `await acquire_attempt_async` | 2 |
| 4 | `backend/app/services/heal_service.py` | 1 处调用改 `await acquire_attempt_async`（:1699） | 2 |

**Owner Decisions 待裁**：
- D-1 方案选择：B（run_in_executor 异步等待 + threading 信号量保留）—— 已在本设计中
  作为推荐，理由见 §2（跨 loop 共享使 asyncio.Semaphore 不可行）；
- D-2 新测试 mock 策略：不 patch 全局 `asyncio.sleep`（吸取 RETRO 教训），仅 mock
  时钟无关的协作用桩；
- D-3 executor 选择：默认 executor（不新建专用池，避免资源口径扩张）。

---

## Owner Freeze 区（施工前由 Owner 填写）

```
SPEC_VERSION: DEBT-SLOT-ASYNC-v1
SPEC_STATUS: FROZEN
READY_FOR_IMPLEMENTATION: YES
APPROVED_BY: OWNER
APPROVED_AT: 2026-10-07 09:41 +08:00
SPEC_SOURCE_SHA: ____________（提交后回填）
SPEC_CONTENT_SHA256: ____________（提交后回填）
```
