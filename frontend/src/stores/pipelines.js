import { ref } from 'vue'
import { defineStore } from 'pinia'
import { pipelineApi } from '@/api/pipelines'

/**
 * 流水线 store（PROJ-V20-CICD）。
 *
 * 只做请求编排 + 缓存；run.status 是后端派生态（由 executions 终态汇总），
 * 前端**只渲染不派生**。
 */
export const usePipelineStore = defineStore('pipelines', () => {
  const items = ref([])
  const runs = ref([])
  const runDetail = ref(null)
  const loading = ref(false)

  async function fetchList(projectId) {
    loading.value = true
    try {
      const res = await pipelineApi.list(projectId)
      items.value = res?.data?.items ?? []
      return items.value
    } finally {
      loading.value = false
    }
  }

  async function create(projectId, payload) {
    const res = await pipelineApi.create(projectId, payload)
    await fetchList(projectId)
    return res?.data
  }

  async function update(pipelineId, payload, projectId) {
    const res = await pipelineApi.update(pipelineId, payload)
    if (projectId) await fetchList(projectId)
    return res?.data
  }

  async function remove(pipelineId, projectId) {
    await pipelineApi.remove(pipelineId)
    return fetchList(projectId)
  }

  async function trigger(pipelineId, body = { trigger_type: 'manual' }, token) {
    const res = await pipelineApi.trigger(pipelineId, body, token)
    return res?.data
  }

  async function fetchRuns(pipelineId) {
    const res = await pipelineApi.runs(pipelineId)
    runs.value = res?.data?.items ?? []
    return runs.value
  }

  async function fetchRunDetail(runId) {
    const res = await pipelineApi.runDetail(runId)
    runDetail.value = res?.data ?? null
    return runDetail.value
  }

  return { items, runs, runDetail, loading, fetchList, create, update, remove, trigger, fetchRuns, fetchRunDetail }
})
