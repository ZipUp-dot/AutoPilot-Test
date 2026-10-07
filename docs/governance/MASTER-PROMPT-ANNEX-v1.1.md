# TRAE-MASTER-PROMPT · §十 预授权例外程序（Owner 2026-10-07 12:35 增补，v1.1）

> 以下 5 类例外按程序自行处置并留痕，**不构成 STOP 上报**；超出程序边界仍 STOP。
> 处置记录写入 STATE.json 的 features[].notes 与 Night Build Report §14。

## E-1 · 迁移 head 漂移（今天 F1 实际发生）
现象：新增迁移后，既有测试硬编码旧 head 常量失败。
程序：仅允许改"版本标记常量"期望值与紧随其说明注释，禁止动任何行为断言；
    白名单临时扩展至该测试文件；处置记录注明"E-1 parity"。

## E-2 · 测试替身接口 parity（DEBT-SLOT-ASYNC 裁定 A 先例）
现象：私有 API 变更导致 mock/spy/替身缺方法。
程序：仅允许为替身补齐委托方法，禁止改替身既有断言与事件序列。

## E-3 · Spec §14 路径笔误
现象：白名单路径与仓库实际路径不符（如漏 services/ 层级）。
程序：修正为仓库实测路径（须附 Source 证据：实测路径 + 类名/tab 名）；
    处置记录写入 Amendment 流水（.Build-B-AMENDMENT-v1.x 序列）并通知 Kimi 补档。

## E-4 · 必填判空口径歧义
现象：validate 类逻辑把合法空值（[] / "" / null）误判缺失。
程序：允许修正判空口径（缺失 = 字段不存在或显式 None；空列表/空串为合法值），
    前提：既有测试全量 GREEN 不降级。

## E-5 · 新增依赖的版本固定
现象：施工确需新 PyPI/npm 依赖。
程序：允许引入，但版本必须固定（== 锁定），且requirements/lockfile 随 Feature 提交；
    选择依据须写入 Spec 附录（Owner 决策缺位时按"成熟度高+测试覆盖好"默认，事后可否决）。

## 不变的前提
- E-1~E-5 全部要求：处置后全量 GREEN 不降级 + 留痕；
- 一 Feature 内同类 E 只许处置一次，第二次同类 = STOP；
- 本清单外的任何白名单外改动 / 断言改动 = 仍然 STOP。
