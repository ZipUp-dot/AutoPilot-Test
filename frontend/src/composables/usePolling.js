import { ref, onUnmounted } from 'vue'

export function usePolling(fn, intervalMs = 2000) {
  const isActive = ref(false)
  let timer = null

  function start() {
    if (isActive.value) return
    isActive.value = true
    const tick = async () => {
      if (!isActive.value) return
      try { await fn() } catch (e) { /* ignore */ }
      if (isActive.value) timer = setTimeout(tick, intervalMs)
    }
    tick()
  }

  function stop() {
    isActive.value = false
    if (timer) { clearTimeout(timer); timer = null }
  }

  onUnmounted(stop)
  return { isActive, start, stop }
}
