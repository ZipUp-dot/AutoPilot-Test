/**
 * EXT-AITC-10B 节拍 1（RED 先行）· store / 展示映射（D-4=A：mock 级）
 *
 * AC-01 三维三列独立、AC-02 新版本、AC-03 评审输入守卫、
 * AC-04 Promote 结果、AC-05 snapshot 复验不一致 → 提示 + 刷新、不自动拒绝。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../src/api/aiDrafts.js', () => ({
  aiDraftsApi: {
    list: vi.fn(), detail: vi.fn(), generate: vi.fn(),
    review: vi.fn(), createVersion: vi.fn(), promote: vi.fn(),
  },
}))

import { aiDraftsApi } from '../../src/api/aiDrafts.js'
import {
  useAiDraftsStore, toDraftRow, validateReviewInput,
} from '../../src/stores/aiDrafts.js'

const _draft = (over = {}) => ({
  id: 7, project_id: 3, snapshot_id: 1, draft_key: 'k1', draft_version: 1,
  case_name: '登录成功', priority: 'P1', steps: '[]',
  ai_assessment: 'recommended', validation_status: 'valid',
  review_status: 'pending', promoted_case_id: null, ...over,
})

describe('AC-01 · 三维三列独立渲染映射（永不合并）', () => {
  it('三个维度可独立取值，互不派生/覆盖', () => {
    const combos = [
      ['recommended', 'valid', 'pending'],
      ['needs_review', 'invalid', 'needs_edit'],
      ['not_ready', 'valid', 'approved'],
    ]
    combos.forEach(([a, v, r]) => {
      const row = toDraftRow(_draft({ ai_assessment: a, validation_status: v, review_status: r }))
      expect(row.ai_assessment).toBe(a)
      expect(row.validation_status).toBe(v)
      expect(row.review_status).toBe(r)
    })
  })
})

describe('AC-03 · 评审输入守卫', () => {
  it('approved 允许空意见', () => {
    expect(validateReviewInput('approved', '').ok).toBe(true)
  })
  it('rejected / needs_edit 必填 comment', () => {
    expect(validateReviewInput('rejected', '').ok).toBe(false)
    expect(validateReviewInput('needs_edit', '   ').ok).toBe(false)
    expect(validateReviewInput('rejected', '步骤不对').ok).toBe(true)
    expect(validateReviewInput('needs_edit', '补充前置').ok).toBe(true)
  })
})

describe('AC-02 · 编辑保存 = 新版本（非覆盖）', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('走 /versions 且刷新后列表出现 v+1', async () => {
    aiDraftsApi.createVersion.mockResolvedValue({ data: _draft({ draft_version: 2 }) })
    aiDraftsApi.list.mockResolvedValue({
      data: { items: [_draft({ draft_version: 1 }), _draft({ draft_version: 2 })], total: 2 },
    })
    const store = useAiDraftsStore()
    await store.saveVersion(7, { case_name: '改名' }, 3)
    expect(aiDraftsApi.createVersion).toHaveBeenCalledWith(7, { case_name: '改名' })
    expect(aiDraftsApi.list).toHaveBeenCalled()
    expect(store.drafts.map(d => d.draft_version)).toEqual([1, 2])
  })
})

describe('AC-04 · Promote 结果', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('成功返回 case_id + source=ai_draft', async () => {
    aiDraftsApi.promote.mockResolvedValue({ data: { case_id: 42, source: 'ai_draft' } })
    const store = useAiDraftsStore()
    const res = await store.promote(7, 3)
    expect(res).toEqual({ case_id: 42, source: 'ai_draft' })
    expect(store.lastPromoted.source).toBe('ai_draft')
  })
})

describe('AC-05 · snapshot 复验不一致：提示 + 刷新，不自动拒绝', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('promote 失败且详情为 needs_review → snapshotNotice + 刷新；绝不调用 rejected', async () => {
    aiDraftsApi.promote.mockRejectedValue(new Error('Evidence Snapshot 已演进，需人工复核后方可 Promote'))
    aiDraftsApi.detail.mockResolvedValue({ data: _draft({ review_status: 'needs_review' }) })
    aiDraftsApi.list.mockResolvedValue({ data: { items: [], total: 0 } })
    const store = useAiDraftsStore()

    await expect(store.promote(7, 3)).rejects.toThrow()
    expect(store.snapshotNotice).toBe(true)
    expect(aiDraftsApi.list).toHaveBeenCalled()
    expect(aiDraftsApi.review).not.toHaveBeenCalled()
  })
})
