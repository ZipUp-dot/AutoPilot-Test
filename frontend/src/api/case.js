import api from './index'

export const caseApi = {
  list(projectId, page = 1, size = 20, keyword = '') {
    return api.get(`/projects/${projectId}/cases/`, { params: { page, size, keyword } })
  },
  detail(projectId, caseId) { return api.get(`/projects/${projectId}/cases/${caseId}`) },
  importExcel(projectId, formData) {
    return api.post(`/projects/${projectId}/cases/import`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    })
  },
  delete(projectId, caseId) { return api.delete(`/projects/${projectId}/cases/${caseId}`) },
  deleteBatch(projectId, ids) { return api.delete(`/projects/${projectId}/cases/`, { data: { ids } }) },
}
