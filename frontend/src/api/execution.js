import api from './index'

export const executionApi = {
  create(projectId, data) { return api.post(`/projects/${projectId}/executions`, data) },
  list(projectId) { return api.get(`/projects/${projectId}/executions`) },
  detail(executionId) { return api.get(`/executions/${executionId}`) },
  status(executionId) { return api.get(`/executions/${executionId}/status`) },
  stop(executionId) { return api.post(`/executions/${executionId}/stop`) },
}
