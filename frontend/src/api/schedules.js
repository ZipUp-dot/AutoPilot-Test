import api from './index'

export const scheduleApi = {
  list(projectId) { return api.get(`/projects/${projectId}/schedules`) },
  create(projectId, data) { return api.post(`/projects/${projectId}/schedules`, data) },
  detail(scheduleId) { return api.get(`/schedules/${scheduleId}`) },
  update(scheduleId, data) { return api.put(`/schedules/${scheduleId}`, data) },
  remove(scheduleId) { return api.delete(`/schedules/${scheduleId}`) },
  enable(scheduleId) { return api.post(`/schedules/${scheduleId}/enable`) },
  disable(scheduleId) { return api.post(`/schedules/${scheduleId}/disable`) },
  trigger(scheduleId) { return api.post(`/schedules/${scheduleId}/trigger`) },
}
