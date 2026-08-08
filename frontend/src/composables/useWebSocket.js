import { ref } from 'vue'

export function useWebSocket() {
  const connected = ref(false)
  const lastMessage = ref(null)
  let ws = null

  function connect() {
    // 预留：WebSocket 实时推送执行进度
    // const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    // ws = new WebSocket(`${protocol}//${location.host}/ws/executions/`)
  }

  function disconnect() {
    if (ws) { ws.close(); ws = null }
    connected.value = false
  }

  return { connected, lastMessage, connect, disconnect }
}
