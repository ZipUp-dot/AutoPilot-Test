# BASELINE-LOCK · Baseline 锁定记录

> Owner 决策：2026-10-07 09:23 +08:00 · 执行：TRAE（本地）· 核验：Kimi（沙箱回读）

## 锁定字段

| 字段 | 值 |
|---|---|
| BASELINE_VERIFIED_SHA | `5b1c903171b1cd26b718ea451da2f3d4079118e8` |
| Baseline Tag | `baseline/20261007`（annotated，对象 `53729dea3a17c454479b3d76e4b09bafe0be35cd`） |
| 验证证据 | 同链全量 pytest：1489 passed / 2 skipped / 0 failed（存档于 `backend/test_output.txt` @ 本 SHA） |
| Frozen Spec | `docs/FROZEN_SPEC_V9.8.1.md` 在场，CONTENT_SHA256 `2d9bb143…a908`（@ `2d80399`） |
| Schema 影响 | 零变更（`8bfba07` 之后 4 个 commit 均为非 Schema） |
| NIGHT_BUILD_BASE_SHA | == `5b1c903`（钉死，施工分支自此切出） |

## 祖先链

```
8bfba07 (tag v9.8.1, R_P2-rc 验收锚点)
  → 58382f8  fix(ai): AI 超时根治（RETRO-HOTFIX-001，@ fix/ai-win-async-timeout）
  → bea014e  chore(repo): 清理 IDE 配置与 npm 缓存
  → 54990ca  docs(retro): RETRO-HOTFIX-001 追认归档
  → 2d80399  docs(spec): Frozen Spec V9.8.1 取证恢复入库
  → 5b1c903  test(verify): 全量复跑存档  ← BASELINE 锁这里
```

## 规程约束（自锁定时刻生效）

1. 施工分支必须命名 `night-build/<BUILD_ID>` 且自 `5b1c903` 切出；
2. Baseline 不可触碰（不变量 #1：Baseline/Night Build Runtime 不得污染正式数据）;
3. GitHub 远端落后于 Gitee（`main` 与 tag 未同步），网络恢复后补推，不影响本锁定效力。

## 待办

- [ ] GitHub 补推（网络恢复后）
- [ ] 立项书 V2.0 入库（Owner 提供后）
- [ ] Matrix 建立并锁 MATRIX_SOURCE_SHA（等立项书 + 候选 Feature 清单裁定）
