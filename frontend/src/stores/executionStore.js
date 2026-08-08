import { defineStore } from 'pinia'
import { ref } from 'vue'
import { executionApi } from '@/api/execution'
import { reportApi } from '@/api/report'

export const useExecutionStore = defineStore('execution', () => {
  const executions = ref([])
  const currentExecution = ref(null)
  const executionStatus = ref(null)
  const pollingTimer = ref(null)
  const loading = ref(false)

  async function fetchExecutions(projectId) {
    const res = await executionApi.list(projectId)
    executions.value = res.data?.items || []
    return res.data
  }

  async function createExecution(projectId, data) {
    const res = await executionApi.create(projectId, data)
    return res.data
  }

  async function fetchStatus(executionId) {
    const res = await executionApi.status(executionId)
    executionStatus.value = res.data
    return res.data
  }

  async function fetchDetail(executionId) {
    loading.value = true
    try {
      const res = await executionApi.detail(executionId)
      currentExecution.value = res.data
      return res.data
    } finally { loading.value = false }
  }

  async function stopExecution(executionId) {
    await executionApi.stop(executionId)
  }

  function startPolling(executionId, intervalMs = 2000) {
    stopPolling()
    fetchStatus(executionId)
    pollingTimer.value = setInterval(() => {
      fetchStatus(executionId).then(status => {
        if (status && ['completed', 'stopped', 'failed'].includes(status.status)) {
          stopPolling()
        }
      })
    }, intervalMs)
  }

  function stopPolling() {
    if (pollingTimer.value) {
      clearInterval(pollingTimer.value)
      pollingTimer.value = null
    }
  }

  return { executions, currentExecution, executionStatus, pollingTimer, loading, fetchExecutions, createExecution, fetchStatus, fetchDetail, stopExecution, startPolling, stopPolling }
})
