import { defineStore } from 'pinia'
import { ref } from 'vue'
import { caseApi } from '@/api/case'
import { generateApi } from '@/api/generate'

export const useCaseStore = defineStore('case', () => {
  const cases = ref([])
  const currentCase = ref(null)
  const importResult = ref(null)
  const generateProgress = ref({ completed: 0, total: 0, status: '' })
  const loading = ref(false)

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
    return res.data
  }

  async function fetchCode(projectId, caseId) {
    const res = await generateApi.getLatestCode(projectId, caseId)
    return res.data
  }

  return { cases, currentCase, importResult, generateProgress, loading, fetchCases, importExcel, deleteCase, deleteBatch, generateCode, generateBatch, fetchCode }
})
