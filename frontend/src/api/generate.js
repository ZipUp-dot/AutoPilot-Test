import api from './index'

export const generateApi = {
  generateCase(projectId, caseId) { return api.post(`/projects/${projectId}/cases/${caseId}/generate`) },
  generateBatch(projectId, caseIds) {
    return api.post(`/projects/${projectId}/cases/generate-batch`, { case_ids: caseIds })
  },
  getBatchStatus(projectId, batchId) {
    return api.get(`/projects/${projectId}/generate-batch/${batchId}/status`)
  },
  getLatestCode(projectId, caseId) { return api.get(`/projects/${projectId}/cases/${caseId}/code`) },
}
