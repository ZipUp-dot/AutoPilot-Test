/**
 * EXT-AITC-10B 节拍 1（RED 先行）· API 契约对齐（10A §6）
 *
 * D-4=A：仅 mock 级测试（vitest + vi.mock），不引入 @vue/test-utils / jsdom。
 * baseURL（/api/v1）由 src/api/index.js 统一持有，此处断言 6 条契约路径后缀。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../../src/api/index.js', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))

import api from '../../src/api/index.js'
import { aiDraftsApi } from '../../src/api/aiDrafts.js'

describe('EXT-AITC-10B · aiDraftsApi 契约（6 端点）', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('generate → POST /ai-drafts/generate {project_id, source_url}', async () => {
    await aiDraftsApi.generate(3, 'https://example.com')
    expect(api.post).toHaveBeenCalledWith('/ai-drafts/generate', {
      project_id: 3, source_url: 'https://example.com',
    })
  })

  it('list → GET /ai-drafts?project_id&review_status', async () => {
    await aiDraftsApi.list(3, 'pending')
    expect(api.get).toHaveBeenCalledWith('/ai-drafts', {
      params: { project_id: 3, review_status: 'pending' },
    })
  })

  it('detail → GET /ai-drafts/{id}', async () => {
    await aiDraftsApi.detail(7)
    expect(api.get).toHaveBeenCalledWith('/ai-drafts/7')
  })

  it('review → PUT /ai-drafts/{id}/review {review_status, comment}', async () => {
    await aiDraftsApi.review(7, 'approved', 'ok')
    expect(api.put).toHaveBeenCalledWith('/ai-drafts/7/review', {
      review_status: 'approved', comment: 'ok',
    })
  })

  it('createVersion → POST /ai-drafts/{id}/versions（新版本，非覆盖）', async () => {
    const payload = { case_name: '改名', steps: [] }
    await aiDraftsApi.createVersion(7, payload)
    expect(api.post).toHaveBeenCalledWith('/ai-drafts/7/versions', payload)
  })

  it('promote → POST /ai-drafts/{id}/promote', async () => {
    await aiDraftsApi.promote(7)
    expect(api.post).toHaveBeenCalledWith('/ai-drafts/7/promote')
  })
})
