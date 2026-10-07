import api from './index'

/**
 * AI 用例候选（EXT-AITC-10B 消费 10A 契约）
 * baseURL（/api/v1）由 src/api/index.js 统一持有。
 */
export const aiDraftsApi = {
  generate(projectId, sourceUrl) {
    return api.post('/ai-drafts/generate', {
      project_id: projectId,
      source_url: sourceUrl,
    })
  },
  list(projectId, reviewStatus) {
    return api.get('/ai-drafts', {
      params: { project_id: projectId, review_status: reviewStatus },
    })
  },
  detail(draftId) {
    return api.get(`/ai-drafts/${draftId}`)
  },
  review(draftId, reviewStatus, comment) {
    return api.put(`/ai-drafts/${draftId}/review`, {
      review_status: reviewStatus,
      comment,
    })
  },
  createVersion(draftId, payload) {
    return api.post(`/ai-drafts/${draftId}/versions`, payload)
  },
  promote(draftId) {
    return api.post(`/ai-drafts/${draftId}/promote`)
  },
}
