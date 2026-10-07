# FEATURE ACCEPTANCE RECORD · PROJ-V20-SCHED（定时执行）

> 机器初验产物 · 2026-10-07 17:50 · 核验人：Kimi（Gitee 分支独立 diff 核验）

## 标识
| 字段 | 值 |
|---|---|
| Feature | PROJ-V20-SCHED（Matrix #4，V2.0 四件套 1/3） |
| Spec | SPEC-PROJ-V20-SCHED-v1（FROZEN 15:25，D-5=30s / D-6=croniter==6.2.4） |
| Commit | 7914488（feat）→ f65828b（STATE 回填） |
| 证据 | 1575 passed / 2 skipped / 0 failed（基线 1528+47，断言零修改，E-1 一例合规） |

## AC → 验证（独立核验）
| AC | 结果 | 独立证据 |
|---|---|---|
| AC-01 坏 cron 拒绝 | PASS | test_cron_validation（6 组参数化） |
| AC-02 走 Admission 唯一入口 | PASS | scheduler_service 仅经 get_orchestrator → run_execute_only；零 execution_state 直调 |
| AC-03 disable 不杀运行中 Execution | PASS | 两实体 stop 字段分离（模型注释 + 集成测试） |
| AC-04 手动/定时同权 | PASS | 同入口（trigger_now 与调度循环共用） |
| AC-05 前后端口径 | PASS | ScheduleManage.vue 渲染后端原值 |
| AC-06 迁移双级证据 | PASS | SQLite upgrade+downgrade + MySQL 实升降（12 列/2 索引/2 FK） |

## 白名单与例外
19 文件全在 §14+E-3/E-5 范围；E-1（head 常量）留痕合规；
路由注册采保守路径（Owner 已批，债记"路由注册统一化"随下批）。

## 结论
READY_CANDIDATE = YES —— 终局 VERIFIED 待 Owner 签字

Owner 签字：____________________  日期：__________
