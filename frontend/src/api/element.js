import api from './index'

export const elementApi = {
  crawl(projectId, maxDepth = 1) { return api.post(`/projects/${projectId}/elements/crawl`, { max_depth: maxDepth }) },
  list(projectId, params = {}) { return api.get(`/projects/${projectId}/elements/`, { params }) },
  clear(projectId) { return api.delete(`/projects/${projectId}/elements/`) },
}
