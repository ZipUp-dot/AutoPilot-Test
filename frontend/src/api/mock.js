import api from './index'

export const mockApi = {
  servers(projectId) { return api.get(`/projects/${projectId}/mock-servers`) },
  createServer(projectId, data) { return api.post(`/projects/${projectId}/mock-servers`, data) },
  server(serverId) { return api.get(`/mock-servers/${serverId}`) },
  updateServer(serverId, data) { return api.put(`/mock-servers/${serverId}`, data) },
  removeServer(serverId) { return api.delete(`/mock-servers/${serverId}`) },

  rules(serverId) { return api.get(`/mock-servers/${serverId}/rules`) },
  createRule(serverId, data) { return api.post(`/mock-servers/${serverId}/rules`, data) },
  updateRule(ruleId, data) { return api.put(`/mock-rules/${ruleId}`, data) },
  removeRule(ruleId) { return api.delete(`/mock-rules/${ruleId}`) },

  // dry-run 匹配校验（无副作用）
  dryRun(serverId, data) { return api.post(`/mock-servers/${serverId}/test`, data) },
}
