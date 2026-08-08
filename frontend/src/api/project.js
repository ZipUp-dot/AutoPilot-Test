import api from './index'

export const projectApi = {
  create(data) { return api.post('/projects/', data) },
  list(page = 1, size = 20) { return api.get('/projects/', { params: { page, size } }) },
  detail(id) { return api.get(`/projects/${id}`) },
  update(id, data) { return api.put(`/projects/${id}`, data) },
  delete(id) { return api.delete(`/projects/${id}`) },
}
