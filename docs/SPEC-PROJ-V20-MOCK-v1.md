# FEATURE-SPEC · PROJ-V20-MOCK · Mock 服务集成

> 状态：**FROZEN**（Owner 2026-10-07 15:25，无新增决策点） · 2026-10-07 · 依赖：无（可与 CICD 并行）
> 输入：Frozen Spec V9.8.1 · v2.1 §十四（MockServer ≠ AndroidMockDriver）· Matrix #6

## 1. Feature Definition

**范围**：内置轻量 HTTP MockServer——用户为被测系统的**外部依赖**配置 Mock 规则
（路径+方法 → 响应），执行时自动将依赖 URL 指向 Mock 服务，实现"无外部依赖"的
隔离测试。
**非目标**：不做 AndroidMockDriver（Android 驱动 Mock，属 Android 域，严格分离——
不变量 #3）；不做 gRPC/TCP Mock（HTTP only，v2 再议）；不做录制回放。

## 2. Existing Code Audit

**复用**：执行环境配置结构（exec_config_json 既有模式）；url_policy 的 host 校验
（Mock 地址注入走同一校验）；Playwright 执行链（路由拦截可用现有 context.route，
**Owner 决策已预填：用 Playwright route 拦截实现，不启真实 HTTP 服务**——零端口占用，
单 worker 内安全）。
**新建**：mock_servers/mock_rules 表 + 规则管理 API + 前端规则页。当前零实现。

## 3. Data Model（Schema Delta = alembic_revision_required，迁移 0007）

**新表 `mock_servers`**（逻辑命名空间，非进程实体）：
id / project_id FK / name / base_path VARCHAR(64)（默认 /mock）/ enabled / created_at。
**新表 `mock_rules`**：
id / server_id FK / method VARCHAR(8)（GET/POST/PUT/DELETE）/
path_pattern VARCHAR(255)（支持 :param 占位）/ status_code INT /
response_body TEXT(JSON) / response_headers TEXT(JSON) nullable /
delay_ms INT 默认 0 / enabled / created_at。
索引：idx_mock_rules_server(server_id, enabled)。

## 4. State Model

rules enabled⇄disabled；执行期拦截是 runtime 行为非持久状态。
派生统计（命中次数）不入库（v2 再议）。

## 5. Data Flow

```
Web 配置规则 → mock_rules CRUD
执行配置（environment config）新增 mock_server_id 可选引用
 → 执行启动：Playwright context.route(base_path/**) 拦截
 → 匹配 method+path_pattern → 返回 status_code/headers/body（+delay）
 → 未匹配 → 放行（不阻断真实请求，记 warning 日志）
```

## 6. Backend

- models + 迁移 0007（只走 Alembic）；
- `services/mock_service.py`：规则加载/匹配引擎（method + 路径模板匹配，
  占位符 `:id` → 任意段）；
- `services/playwright_service.py` 小改点：执行配置含 mock_server_id 时注册
  route 拦截（**白名单点，唯一改动处**）；appium_service 不动（Android 域隔离）；
- `routers/mock.py`：servers/rules CRUD + 规则测试（dry-run 匹配校验，无副作用）。

## 7. Frontend

- `src/views/MockManage.vue`：server 列表 + 规则编辑器（方法/路径模板/状态码/
  JSON 体编辑器/延迟）+ dry-run 匹配测试按钮；
- api/mock.js + store + 菜单；执行配置表单增加"Mock 服务"下拉（可空）。

## 8. Integration

- 仅 Web 执行链（Playwright）注入 Mock；Android 链禁用（appium 不动，§十四分离）；
- 执行走既有 Admission 唯一入口，Mock 只是环境配置的一部分（不建第二执行入口）；
- 报告/指标零感知（Mock 对执行结果透明）。

## 9. KPI Impact

Original KPI 不改。Engineering Metrics：Mock 命中率（日志派生，v2 入 metrics）。

## 10. Schema Delta

alembic_revision_required：0007_mock_services（两表 + 索引）。零 backfill。

## 11. Acceptance Criteria

| AC | 内容 | acceptance_method |
|---|---|---|
| AC-01 | 规则 CRUD + 路径模板校验（非法模式 422） | 参数化单测 |
| AC-02 | 执行时 mock 拦截生效：匹配规则返回定制响应 | 集成测试（Playwright context mock） |
| AC-03 | 未匹配路径放行真实请求（不阻断） | 集成测试 |
| AC-04 | delay_ms 生效（墙钟 ≥ delay） | 单测（mock 时钟） |
| AC-05 | appium_service 零改动（diff 核验） | 硬边界 |
| AC-06 | 全量回归 GREEN + 0007 alembic 级证据 | 硬边界增补 4 |

## 12. Acceptance Tests

tests/unit/test_mock_matching.py / tests/services/test_mock_service.py /
tests/routers/test_mock.py / tests/integration/test_mock_injection.py；RED 先行。

## 13. Risk Audit

| 风险 | 对策 | 依据 |
|---|---|---|
| Mock 拦截误伤真实流量 | 仅拦截 base_path 前缀；未匹配放行 | 工程经验 |
| Playwright route 与既有导航感知冲突 | 拦截注册点在页面加载前，单点注入 | 代码事实 |
| Android 域被波及 | AC-05 硬锁定 appium_service 零改动 | §十四；不变量 #3 |
| 规则膨胀性能 | 每 server 规则数 <100 不优化；超限 STOP 上报 | 不变量 #2（数字 Owner 可调整） |

## 14. File Modification Scope

顺序：RED 测试 → 0007 迁移 → models → mock_service → routers →
playwright_service 单点注入（白名单）→ 前端（view/api/store/执行配置表单）
→ GREEN → Smoke → commit feat(mock): Mock 服务集成（PROJ-V20-MOCK）。
Owner Decisions：无新增（拦截实现已预填 Playwright route，无新端口无新进程）。
