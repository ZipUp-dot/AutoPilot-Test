/**
 * EXT-V12-AGGREG-FE 节拍 1（RED 先行）· metrics API 契约
 *
 * 锚定 docs/night-build/VERIFY-789-REPORT.md #9：后端 /metrics 已就绪，前端未接线。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../../src/api/index.js', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))

import api from '../../src/api/index.js'
import { metricsApi } from '../../src/api/metrics.js'

describe('EXT-V12-AGGREG-FE · metricsApi', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('overview(projectId) → GET /metrics?project_id', async () => {
    await metricsApi.overview(3)
    expect(api.get).toHaveBeenCalledWith('/metrics', { params: { project_id: 3 } })
  })

  it('overview() 无 project_id → 全项目口径', async () => {
    await metricsApi.overview()
    expect(api.get).toHaveBeenCalledWith('/metrics', { params: { project_id: undefined } })
  })
})
