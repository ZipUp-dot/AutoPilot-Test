<template>
  <div v-if="visible" class="loading-mask">
    <el-icon class="loading-icon" :size="32"><Loading /></el-icon>
    <p v-if="text">{{ text }}</p>
  </div>
</template>

<script setup>
defineProps({ visible: { type: Boolean, default: false }, text: { type: String, default: '' } })
</script>

<style scoped>
.loading-mask { position: absolute; inset: 0; z-index: 100; display: flex; flex-direction: column; align-items: center; justify-content: center; background: rgba(255,255,255,.85); border-radius: 8px; }
.loading-icon { animation: spin 1s linear infinite; color: var(--primary); }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
