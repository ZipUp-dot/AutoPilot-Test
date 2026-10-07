import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { metricsApi } from '@/api/metrics'

/**
 * 四条口径路径（锚定 VERIFY-789-REPORT.md #9，字段级钉死）：
 *   overview.first_generation_success_rate
 *   overview.final_success_rate
 *   overview.pipeline_coverage.coverage          ← 嵌套内层键
 *   overview.execution_start_coverage.coverage   ← 嵌套内层键
 *
 * 注意：取 overview.coverage 会得 undefined —— 禁止回落扁平键。
 */
export const KPI_PATHS = [
  'overview.first_generation_success_rate',
  'overview.final_success_rate',
  'overview.pipeline_coverage.coverage',
  'overview.execution_start_coverage.coverage',
]

/**
 * 只取后端原值（不派生）：缺失 → null（不得伪造 0，0 与 null 语义不同）。
 */
export function pickKpis(overview) {
  const o = overview || {}
  return {
    firstGenerationRate: o.first_generation_success_rate?.rate ?? null,
    finalSuccessRate: o.final_success_rate?.rate ?? null,
    pipelineCoverage: o.pipeline_coverage?.coverage ?? null,
    executionStartCoverage: o.execution_start_coverage?.coverage ?? null,
  }
}

/** 仅展示格式化：null/undefined → '—'；数值按百分比渲染（不改变口径） */
export function formatRate(rate) {
  if (rate === null || rate === undefined) return '—'
  return `${(rate * 100).toFixed(1)}%`
}

export const useMetricsStore = defineStore('metrics', () => {
  const overview = ref(null)
  const loading = ref(false)

  // 只渲染不派生：kpis 直接取后端四路径原值
  const kpis = computed(() => pickKpis(overview.value))

  async function fetchOverview(projectId) {
    loading.value = true
    try {
      const res = await metricsApi.overview(projectId)
      overview.value = res?.data ?? null
      return overview.value
    } finally {
      loading.value = false
    }
  }

  return { overview, loading, kpis, fetchOverview }
})
