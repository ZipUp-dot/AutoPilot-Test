import api from './index'

export const pipelineApi = {
  list(projectId) { return api.get(`/projects/${projectId}/pipelines`) },
  create(projectId, data) { return api.post(`/projects/${projectId}/pipelines`, data) },
  detail(pipelineId) { return api.get(`/pipelines/${pipelineId}`) },
  update(pipelineId, data) { return api.put(`/pipelines/${pipelineId}`, data) },
  remove(pipelineId) { return api.delete(`/pipelines/${pipelineId}`) },
  // webhook 触发需带 X-Pipeline-Token；手动触发无需 token
  trigger(pipelineId, body, token) {
    const headers = token ? { 'X-Pipeline-Token': token } : undefined
    return api.post(`/pipelines/${pipelineId}/trigger`, body, { headers })
  },
  runs(pipelineId) { return api.get(`/pipelines/${pipelineId}/runs`) },
  runDetail(runId) { return api.get(`/pipeline-runs/${runId}`) },
}
