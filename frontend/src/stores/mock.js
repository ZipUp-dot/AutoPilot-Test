import { ref } from 'vue'
import { defineStore } from 'pinia'
import { mockApi } from '@/api/mock'

/**
 * Mock 服务 store（PROJ-V20-MOCK）。
 *
 * Mock 是逻辑命名空间（无端口无进程）：执行期由 Playwright route 拦截注入。
 * 前端只做 CRUD + dry-run 展示，不参与匹配判定（判定以后端为准）。
 */
export const useMockStore = defineStore('mock', () => {
  const servers = ref([])
  const rules = ref([])
  const loading = ref(false)

  async function fetchServers(projectId) {
    loading.value = true
    try {
      const res = await mockApi.servers(projectId)
      servers.value = res?.data?.items ?? []
      return servers.value
    } finally {
      loading.value = false
    }
  }

  async function createServer(projectId, payload) {
    const res = await mockApi.createServer(projectId, payload)
    await fetchServers(projectId)
    return res?.data
  }

  async function updateServer(serverId, payload, projectId) {
    const res = await mockApi.updateServer(serverId, payload)
    if (projectId) await fetchServers(projectId)
    return res?.data
  }

  async function removeServer(serverId, projectId) {
    await mockApi.removeServer(serverId)
    return fetchServers(projectId)
  }

  async function fetchRules(serverId) {
    const res = await mockApi.rules(serverId)
    rules.value = res?.data?.items ?? []
    return rules.value
  }

  async function createRule(serverId, payload) {
    const res = await mockApi.createRule(serverId, payload)
    await fetchRules(serverId)
    return res?.data
  }

  async function updateRule(ruleId, payload, serverId) {
    const res = await mockApi.updateRule(ruleId, payload)
    if (serverId) await fetchRules(serverId)
    return res?.data
  }

  async function removeRule(ruleId, serverId) {
    await mockApi.removeRule(ruleId)
    return fetchRules(serverId)
  }

  async function dryRun(serverId, payload) {
    const res = await mockApi.dryRun(serverId, payload)
    return res?.data
  }

  return {
    servers, rules, loading,
    fetchServers, createServer, updateServer, removeServer,
    fetchRules, createRule, updateRule, removeRule, dryRun,
  }
})
