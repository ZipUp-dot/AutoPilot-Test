# QUEUE-SNAPSHOT · BUILD nb-20261007-C（FROZEN）

> Owner 冻结：2026-10-07 15:25 · 3×READY · 分支基点 = Step 0 完成后 docs 最新 commit
> NIGHT_BUILD_BASE：Build-B 合并后 main（04657d65bf30aed63ea481c950a4d16604389a97）

| # | feature_id | spec_sha | dependency | status | priority |
|---|---|---|---|---|---|
| 1 | PROJ-V20-SCHED | a3af0b0dd1166870473910e11a27fe134951ccd1f64e9128ac8279acda9b7a38 | 无 | READY | P0 |
| 2 | PROJ-V20-CICD | 5be226ba75c79c68933d13c07938b7e9b4f3635df391e2b4a5ae5371fbb1379b | SCHED（0005 迁移先行） | READY | P1 |
| 3 | PROJ-V20-MOCK | 1f8f42d237de876c5c552d84ea2f3d2a265d7ffcff59085a9a73023b00be0a45 | 无（可与 CICD 并行） | READY | P1 |
| — | PROJ-V20-USER | 设计未出 | — | NOT_READY | — |
| — | DEBT-PRIVATE-API | 设计未出 | — | NOT_READY | — |
| — | Android 真机 | 硬件未备 | — | BLOCKED | — |

施工序：SCHED → CICD → MOCK；迁移编号 0005→0006→0007（冲突 = STOP）。
