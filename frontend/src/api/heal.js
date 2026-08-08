import api from './index'

export const healApi = {
  triggerHeal(executionId, caseId, stepIndex) {
    return api.post(`/executions/${executionId}/heal`, { case_id: caseId, step_index: stepIndex })
  },
  getHealRecords(executionId) { return api.get(`/executions/${executionId}/heal-records`) },
}
