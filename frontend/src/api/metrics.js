import api from './index'

/**
 * KPI 聚合指标（EXT-V12-AGGREG-FE）
 * 后端唯一来源：GET /api/v1/metrics（只读聚合，前端不做任何派生计算）。
 */
export const metricsApi = {
  overview(projectId) {
    return api.get('/metrics', { params: { project_id: projectId } })
  },
}
