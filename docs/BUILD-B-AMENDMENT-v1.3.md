# BUILD-B-AMENDMENT-v1.3 · F1 节拍 0 STOP 解除（Owner 裁定 2026-10-07 12:05）

> 依据：TRAE F1 节拍 0 上报 S-3 / S-4 · 本文件随本次回复生效，无需升 Queue 版本

## S-3 裁定 · §14 白名单路径修正（2 处）

| §14 行 | 原写法（错误） | 修正为（TRAE Source 证据） |
|---|---|---|
| row 6 | app/ai_service.py | app/services/ai_service.py |
| row 8 | app/models/page_elements.py | app/models/element.py（class PageElement，__tablename__="page_elements"） |

授权：按修正后路径施工，S-3 解除。

## S-4 裁定 · D-1/D-2/D-3 补裁 + D-3 文本修正

三项决策 Owner 已于 2026-10-07 11:01 口头裁定（"按建议来整"），原 Spec 中"待裁"
标记系文档滞后，现正式落盘：
- D-1 = 按元素分区多次生成（已裁）；
- D-2 = 允许改 steps 结构，强制新版本 + 重新 System Validation（已裁）；
- D-3 修正文本："迁移 0004 在 F1 完成（脚本 + upgrade 一次提交），10B 同批施工
  （同一 Build 批次，非文件归属）"。原"迁移 0003"字样作废。

## 附带事项
1. STATE.json 中 F1=RUNNING 合并于初始化提交（4ea06a9）：维持现状，如实反映进度，不 amend。
2. 总控手册文件此前未入库（Owner 疏忽），现随本裁定补入：docs/governance/TRAE-MASTER-PROMPT.md，
   并由 TRAE 补写 §六 沙箱/本机协作条款（措辞沿用 TRAE 建议稿）。
3. 本 Amendment 由 TRAE 单独 commit：docs(amendment): BUILD-B-AMENDMENT v1.3（F1 节拍 0 STOP 解除）。
