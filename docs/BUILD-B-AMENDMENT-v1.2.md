# BUILD-B-AMENDMENT-v1.2 · 施工单二次增补（Owner 裁定 2026-10-07 11:25）

> 依据：TRAE v1.2 建议稿（P2-1/2/3）· 本文件与 Queue v1.2 一并冻结，取代 v1.1 中相冲突条目

## 裁定记录

| # | 项 | Owner 裁定 |
|---|---|---|
| P2-1 | F4 dual-task 缺 C-22 审计基准 | ✅ F5 Spec（SPEC-BUG-SECOND-CONTRACT-v1.md）以"只读参考、不施工、不入哈希"身份一并放入 docs/；F4 节拍 0 据此重定位 |
| P2-2 | 迁移测试盲区：conftest 用 create_all，alembic 链测试零执行 | ✅ F1 验收追加两条：① 扩展 tests/unit/test_alembic_migration.py 覆盖 0004（upgrade + downgrade）；② Owner 本机在 scratch MySQL 实测 alembic upgrade head 并存证。**硬边界增补 4：迁移类 Feature 的 GREEN 必须含 alembic 级测试证据，create_all 的 GREEN 不构成迁移正确性证明** |
| P2-3 | F3 JSONPath 未钉精确（coverage 嵌套内层） | ✅ F3 验收素材写死四个 JSONPath：overview.first_generation_success_rate / overview.final_success_rate / overview.pipeline_coverage.coverage / overview.execution_start_coverage.coverage |
| 流程修正 | TRAE 建议稿 Step 0 自相矛盾（"Owner 先推送" vs "push Owner 执行"） | ✅ 统一为：Owner 仅把 7 个文件放入工作区（不推送）；TRAE 核验+逐个 commit 并输出命令清单；Owner 执行 push；TRAE pull 后记录 NEW_BASE 拉分支 |
| F5 后续建议 | TRAE 建议 C-22 重定位后优先"删"而非"改挂" | ⏸ 知悉，待重定位证据出来后由 Owner 裁定，本文件不预决 |

## 硬边界增补（第 4 条）
迁移类 Feature 的 GREEN 必须含 alembic 级测试证据（upgrade+downgrade 覆盖）；
仅 create_all 的全量 GREEN 不构成迁移正确性证明。
