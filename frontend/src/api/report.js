import api from './index'

export const reportApi = {
  generate(executionId) { return api.post(`/executions/${executionId}/reports/generate`) },
  getInfo(executionId) { return api.get(`/executions/${executionId}/reports`) },
}
