# FEATURE_FREEZE_MANIFEST · DEBT-SLOT-ASYNC

| 字段 | 值 |
|---|---|
| feature_id | DEBT-SLOT-ASYNC |
| spec_version | DEBT-SLOT-ASYNC-v1.1 |
| spec_path | docs/SPEC-DEBT-SLOT-ASYNC-v1.1.md |
| spec_source_sha | a197fb923eefd132a349fb92a60627cfd7c183e8 |
| spec_content_sha256 | 8e3375652e3ee9cae11acbe15285d33fd2cd8baa067c1c998c6081228f5a6c5f |
| approved_by | OWNER |
| approved_at | 2026-10-07 09:41 +08:00 |
| base_sha | 5b1c903171b1cd26b718ea451da2f3d4079118e8（NIGHT_BUILD_BASE_SHA，钉死） |
| matrix_source_sha | 5873c664b278f4361e52e12b11eeb35dfdb01b6e |
| frozen_spec_ref | V9.8.1 / docs/FROZEN_SPEC_V9.8.1.md / content 2d9bb143…a908 |

施工前 Gate D（局部）验证项：
1. spec 文件实际字节 SHA256 == 本表 spec_content_sha256；
2. spec_source_sha 回填后与本 manifest 一致；
3. SPEC_STATUS=FROZEN 且 APPROVED_BY=OWNER。
任一不符 → NOT_READY，禁止施工。
