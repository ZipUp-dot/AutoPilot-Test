# BUILD-B-AMENDMENT-v1.4 · F1 节拍 3 回归处置（Owner 裁定 2026-10-07 12:40）

> 依据：TRAE 节拍 3 全量回归 STOP（test_migration.py head 漂移）· 援引先例：裁定 A（测试替身 parity）

## 事实
tests/unit/test_migration.py::TestLegacyBridgeBehavior::
test_legacy_compatible_db_stamped_and_upgraded 断言 `v == "0003_batch_jobs"`。
F1 新增迁移 0004_ai_case_drafts 后 alembic head 前移，该断言失败——属 head 标记常量漂移，
与实现行为无关（1510 passed 中唯一 failed，其余全绿）。

## 裁定（援引裁定 A 先例：parity 修正 ≠ 改断言制造 PASS）
1. **授权修正**，严格限定两处：
   a. 期望值 "0003_batch_jobs" → "0004_ai_case_drafts"；
   b. 其上方注释「只应用 0002 delta」→「0002/0003/0004 delta」。
2. **禁改**该文件其他任何行；
3. F1 §14 白名单追加：`backend/tests/unit/test_migration.py`（依据本 Amendment）；
4. 判定标准（写进验收记录）：该断言性质 = 版本标记常量校验，非行为断言；
   修正方向 = 追随真实 head（ GREEN 因实现正确而达成），非反向修断言保绿。
