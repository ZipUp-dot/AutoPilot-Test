import { ref } from 'vue'
import { defineStore } from 'pinia'
import { scheduleApi } from '@/api/schedules'

/**
 * 定时任务 store（PROJ-V20-SCHED）。
 *
 * 只做请求编排 + 列表缓存；cron 人类可读回显是纯展示派生（不入库、不回写后端）。
 * 「停止调度」(disable) 与「停止执行」(Execution Stop) 是两套语义：
 * disable 不改动运行中的 Execution —— 由后端保证，前端文案同步提示。
 */
/**
 * cron 表达式的「人类可读」展示派生（只读展示；不入库、不回写）。
 * 仅覆盖常见形态，未识别时原样返回表达式（不猜测）。
 */
const _DOW = { 0: '周日', 7: '周日', 1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六' }

function _dow(v) {
  if (v.includes('-')) {
    const [a, b] = v.split('-')
    return `${_DOW[a] ?? a}至${_DOW[b] ?? b}`
  }
  return _DOW[v] ?? `周${v}`
}

export function describeCron(expr) {
  const raw = (expr || '').trim()
  const f = raw.split(/\s+/)
  if (f.length !== 5) return raw || '—'
  const [mi, ho, dom, mon, dow] = f
  if (raw === '* * * * *') return '每分钟'
  if (mi.startsWith('*/') && ho === '*' && dom === '*' && mon === '*' && dow === '*') {
    return `每 ${mi.slice(2)} 分钟`
  }
  if (mi === '0' && ho.startsWith('*/') && dom === '*' && mon === '*' && dow === '*') {
    return `每 ${ho.slice(2)} 小时`
  }
  if (/^\d+$/.test(mi) && /^\d+$/.test(ho)) {
    const hm = `${ho.padStart(2, '0')}:${mi.padStart(2, '0')}`
    if (dom === '*' && mon === '*' && dow === '*') return `每天 ${hm}`
    if (dom === '*' && mon === '*' && dow !== '*') return `每${_dow(dow)} ${hm}`
    if (dom !== '*' && mon === '*' && dow === '*') return `每月 ${dom} 日 ${hm}`
  }
  return raw
}

export const useScheduleStore = defineStore('schedules', () => {
  const items = ref([])
  const loading = ref(false)

  async function fetchList(projectId) {
    loading.value = true
    try {
      const res = await scheduleApi.list(projectId)
      items.value = res?.data?.items ?? []
      return items.value
    } finally {
      loading.value = false
    }
  }

  async function create(projectId, payload) {
    const res = await scheduleApi.create(projectId, payload)
    await fetchList(projectId)
    return res?.data
  }

  async function update(scheduleId, payload, projectId) {
    const res = await scheduleApi.update(scheduleId, payload)
    await fetchList(projectId)
    return res?.data
  }

  async function remove(scheduleId, projectId) {
    await scheduleApi.remove(scheduleId)
    return fetchList(projectId)
  }

  async function enable(scheduleId, projectId) {
    await scheduleApi.enable(scheduleId)
    return fetchList(projectId)
  }

  async function disable(scheduleId, projectId) {
    await scheduleApi.disable(scheduleId)
    return fetchList(projectId)
  }

  async function trigger(scheduleId) {
    const res = await scheduleApi.trigger(scheduleId)
    return res?.data
  }

  return { items, loading, fetchList, create, update, remove, enable, disable, trigger }
})
