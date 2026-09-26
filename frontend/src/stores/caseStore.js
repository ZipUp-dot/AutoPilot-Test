import { defineStore } from 'pinia'
import { ref } from 'vue'
import { caseApi } from '@/api/case'
import { generateApi } from '@/api/generate'

export const useCaseStore = defineStore('case', () => {
  const cases = ref([])
  const currentCase = ref(null)
  const importResult = ref(null)
  const generateProgress = ref({ completed: 0, total: 0, status: '', batchId: '' })
  const loading = ref(false)
  let _pollTimer = null

  async function fetchCases(projectId, params = {}) {
    loading.value = true
    try {
      const res = await caseApi.list(projectId, params.page || 1, params.size || 50, params.keyword || '')
      cases.value = res.data?.items || []
      return res.data
    } finally { loading.value = false }
  }

  async function importExcel(projectId, formData) {
    loading.value = true
    try {
      const res = await caseApi.importExcel(projectId, formData)
      importResult.value = res.data
      return res.data
    } finally { loading.value = false }
  }

  async function deleteCase(projectId, caseId) {
    await caseApi.delete(projectId, caseId)
  }

  async function deleteBatch(projectId, ids) {
    await caseApi.deleteBatch(projectId, ids)
  }

  async function generateCode(projectId, caseId) {
    const res = await generateApi.generateCase(projectId, caseId)
    return res.data
  }

  async function generateBatch(projectId, caseIds) {
    const res = await generateApi.generateBatch(projectId, caseIds)
    const { batch_id, total } = res.data

    generateProgress.value = { completed: 0, total, status: 'running', batchId: batch_id }

    // 轮询进度，每 2 秒一次
    _stopPolling()
    _pollTimer = setInterval(async () => {
      let data = null
      try {
        const statusRes = await generateApi.getBatchStatus(projectId, batch_id)
        // ApiResponse 包装：code!=0 时 data 可能为 null
        data = statusRes && statusRes.data
      } catch {
        data = null
      }

      // 批次丢失（如后端重启导致内存任务清空）或接口异常：
      // 不再无限轮询卡死，回查真实数据并把进度条收起，恢复到实际状态。
      if (!data || (data.code !== undefined && data.code !== 0)) {
        _stopPolling()
        await finishBatchAndRefresh(projectId)
        return
      }

      generateProgress.value = {
        completed: data.completed || 0,
        failed: data.failed || 0,
        total: data.total || total,
        status: data.status,
        progressPct: data.progress_pct || 0,
        batchId: batch_id,
      }
      if (data.status === 'completed') {
        _stopPolling()
        await finishBatchAndRefresh(projectId)
      }
    }, 2000)

    return res.data
  }

  async function finishBatchAndRefresh(projectId) {
    // 批量结束（正常完成 / 批次丢失 / 异常）：收起进度条并按真实状态刷新用例列表
    generateProgress.value = { completed: 0, total: 0, status: '', batchId: '' }
    try {
      await fetchCases(projectId)
    } catch { /* 刷新失败不阻断 */ }
  }

  async function fetchCode(projectId, caseId) {
    const res = await generateApi.getLatestCode(projectId, caseId)
    return res.data
  }

  function _stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer)
      _pollTimer = null
    }
  }

  return { cases, currentCase, importResult, generateProgress, loading, fetchCases, importExcel, deleteCase, deleteBatch, generateCode, generateBatch, fetchCode }
})
