import { defineStore } from 'pinia'
import { ref } from 'vue'
import { aiDraftsApi } from '@/api/aiDrafts'

/**
 * 三维三列独立展示映射（8.5 永不合并）：
 * ai_assessment / validation_status / review_status 原样透传，
 * 互不派生、互不覆盖；前端不做状态机推断。
 */
export function toDraftRow(draft) {
  const d = draft || {}
  return {
    id: d.id,
    draft_key: d.draft_key,
    draft_version: d.draft_version,
    case_name: d.case_name,
    priority: d.priority,
    ai_assessment: d.ai_assessment ?? null,
    validation_status: d.validation_status ?? null,
    review_status: d.review_status ?? null,
    promoted_case_id: d.promoted_case_id ?? null,
  }
}

/**
 * 人工评审输入守卫（AC-03）：
 * approved 免填意见；rejected / needs_edit 必须填写 review_comment。
 */
export function validateReviewInput(reviewStatus, comment) {
  if (!reviewStatus) return { ok: false, error: '请选择评审动作' }
  const needComment = reviewStatus === 'rejected' || reviewStatus === 'needs_edit'
  if (needComment && !String(comment ?? '').trim()) {
    return { ok: false, error: '驳回 / 需修改必须填写评审意见' }
  }
  return { ok: true, error: null }
}

export const useAiDraftsStore = defineStore('aiDrafts', () => {
  const drafts = ref([])
  const current = ref(null)
  const loading = ref(false)
  const snapshotNotice = ref(false)
  const lastPromoted = ref(null)

  async function fetchDrafts(projectId, reviewStatus = null) {
    loading.value = true
    try {
      const res = await aiDraftsApi.list(projectId, reviewStatus)
      drafts.value = (res?.data?.items || []).map(toDraftRow)
      return drafts.value
    } finally {
      loading.value = false
    }
  }

  async function fetchDetail(draftId) {
    const res = await aiDraftsApi.detail(draftId)
    current.value = res?.data || null
    return current.value
  }

  /** 编辑保存 = 产生新版本（D-2），旧版本只读；保存后刷新列表 */
  async function saveVersion(draftId, payload, projectId) {
    const res = await aiDraftsApi.createVersion(draftId, payload)
    current.value = res?.data || current.value
    if (projectId !== undefined) await fetchDrafts(projectId)
    return res?.data || null
  }

  async function submitReview(draftId, reviewStatus, comment) {
    const check = validateReviewInput(reviewStatus, comment)
    if (!check.ok) throw new Error(check.error)
    const res = await aiDraftsApi.review(draftId, reviewStatus, comment)
    await fetchDetail(draftId)
    return res?.data || null
  }

  /**
   * Promote：后端 snapshot 复验不一致时返回失败并置 needs_review（8.2）。
   * 前端只做提示 + 刷新，**禁止自动拒绝**。
   */
  async function promote(draftId, projectId) {
    snapshotNotice.value = false
    try {
      const res = await aiDraftsApi.promote(draftId)
      lastPromoted.value = res?.data || null
      return lastPromoted.value
    } catch (err) {
      let detail = null
      try {
        detail = await fetchDetail(draftId)
      } catch {
        detail = null
      }
      if (detail?.review_status === 'needs_review') {
        snapshotNotice.value = true
        if (projectId !== undefined) await fetchDrafts(projectId)
      }
      throw err
    }
  }

  return {
    drafts, current, loading, snapshotNotice, lastPromoted,
    fetchDrafts, fetchDetail, saveVersion, submitReview, promote,
  }
})
