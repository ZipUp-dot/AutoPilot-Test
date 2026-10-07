# FEATURE ACCEPTANCE RECORD · PROJ-V20-CICD（可视化 CI/CD 集成）

> 机器初验产物 · 2026-10-07 18:10 · 核验人：Kimi（Gitee 分支独立 diff 核验）

## 标识
| 字段 | 值 |
|---|---|
| Feature | PROJ-V20-CICD（Matrix #3，V2.0 四件套 2/3） |
| Spec | SPEC-PROJ-V20-CICD-v1（FROZEN 15:25）+ Owner 三处置裁定（18:05） |
| Commit | d867354（feat 20 文件）→ 83f485a（STATE 回填） |
| 证据 | 1604 passed / 2 skipped / 0 failed（基线 1575+29，断言零修改，E-1 一例） |

## AC → 验证（独立核验）
| AC | 结果 | 独立证据 |
|---|---|---|
| AC-01 非法 token 401 零 run | PASS | test_pipeline_token；Smoke 实测缺/错 token 均 401 |
| AC-02 触发走 Admission（零直建） | PASS | pipeline_service diff 核验：execution 事实由 Admission 创建；集成测试 |
| AC-03 防重入 409 | PASS | Smoke 实测 running 中触发 409 |
| AC-04 run.status 派生正确 | PASS | refresh_run_status 唯一 writer（Owner 裁定等价方案）；集成测试 |
| AC-05 视图口径一致 | PASS | PipelineRuns.vue 渲染后端原值 |
| AC-06 迁移双级证据 | PASS | 0006 SQLite 升降 + MySQL 实升降（FK 删除顺序修复留痕） |

## 关键红线核验（diff 级）
- executions 仅增列 pipeline_run_id（可空 FK RESTRICT，注释明示"不构成第二 Contract"）——Option A 追认；
- execution_finalizer.py 零改动（0 行）——无第二状态出口；
- token 常量时间比较（hmac.compare_digest）；
- 20 文件全在白名单 + Owner 三处置范围内。

## 结论
READY_CANDIDATE = YES —— 终局 VERIFIED 待 Owner 签字

Owner 签字：____________________  日期：__________
