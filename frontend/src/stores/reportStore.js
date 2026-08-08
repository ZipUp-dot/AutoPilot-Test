import { defineStore } from 'pinia'
import { ref } from 'vue'
import { reportApi } from '@/api/report'

export const useReportStore = defineStore('report', () => {
  const reports = ref([])
  const currentReport = ref(null)
  const loading = ref(false)

  async function fetchReports(executionIds) {
    // 批量获取报告信息
    const results = []
    for (const eid of executionIds) {
      try {
        const res = await reportApi.getInfo(eid)
        if (res.data) results.push(res.data)
      } catch { /* 报告未生成 */ }
    }
    reports.value = results
    return results
  }

  async function fetchReport(executionId) {
    loading.value = true
    try {
      const res = await reportApi.getInfo(executionId)
      currentReport.value = res.data
      return res.data
    } finally { loading.value = false }
  }

  async function generateReport(executionId) {
    const res = await reportApi.generate(executionId)
    return res.data
  }

  function downloadReport(url) {
    window.open(url, '_blank')
  }

  return { reports, currentReport, loading, fetchReports, fetchReport, generateReport, downloadReport }
})
