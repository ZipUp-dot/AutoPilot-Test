/**
 * EXT-V12-AGGREG-FE 节拍 1（RED 先行）· 四 JSONPath 字段级断言 + 只渲染不派生
 *
 * 锚定 VERIFY-789-REPORT.md #9。coverage 是嵌套内层键：
 * 取 overview.coverage 得 undefined —— 必须显式 fail。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../src/api/metrics.js', () => ({
  metricsApi: { overview: vi.fn() },
}))

import { metricsApi } from '../../src/api/metrics.js'
import {
  useMetricsStore, pickKpis, formatRate, KPI_PATHS,
} from '../../src/stores/metrics.js'

/** 后端 metrics_service.overview() 真实返回形态（实测字段） */
const BACKEND = {
  first_generation_success_rate: { rate: 0.72, numerator: 18, denominator: 25 },
  final_success_rate: { rate: 0.85, numerator: 17, denominator: 20 },
  pipeline_coverage: { coverage: 0.6, ready: 3, total: 5 },
  execution_start_coverage: { coverage: 0.9, started: 9, total: 10 },
}

describe('AC · 四条 JSONPath 钉死', () => {
  it('KPI_PATHS 精确等于 Owner 指定的四条路径', () => {
    expect(KPI_PATHS).toEqual([
      'overview.first_generation_success_rate',
      'overview.final_success_rate',
      'overview.pipeline_coverage.coverage',
      'overview.execution_start_coverage.coverage',
    ])
  })

  it('四个取值逐字段正确（含嵌套内层 coverage）', () => {
    const k = pickKpis(BACKEND)
    expect(k.firstGenerationRate).toBe(0.72)
    expect(k.finalSuccessRate).toBe(0.85)
    expect(k.pipelineCoverage).toBe(0.6)
    expect(k.executionStartCoverage).toBe(0.9)
  })

  it('嵌套内层键钉死：扁平 overview.coverage 必为 undefined，且不得回落', () => {
    expect(BACKEND.coverage).toBeUndefined()
    const flat = pickKpis({ coverage: 0.99 })
    expect(flat.pipelineCoverage).toBeNull()
    expect(flat.executionStartCoverage).toBeNull()
  })

  it('缺键 / null → null（不伪造 0）', () => {
    const k = pickKpis({ first_generation_success_rate: { rate: null } })
    expect(k.firstGenerationRate).toBeNull()
    expect(k.finalSuccessRate).toBeNull()
  })
})

describe('AC · 只渲染不派生', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('store 原样透传后端 overview，不做任何计算', async () => {
    metricsApi.overview.mockResolvedValue({ data: BACKEND })
    const store = useMetricsStore()
    await store.fetchOverview(3)
    expect(store.overview).toEqual(BACKEND)
    expect(store.kpis.firstGenerationRate).toBe(0.72)
    expect(store.kpis.pipelineCoverage).toBe(0.6)
  })

  it('formatRate 仅做展示格式化：null → —，0 不得与 null 混淆', () => {
    expect(formatRate(null)).toBe('—')
    expect(formatRate(undefined)).toBe('—')
    expect(formatRate(0)).toBe('0.0%')
    expect(formatRate(0.72)).toBe('72.0%')
  })
})
