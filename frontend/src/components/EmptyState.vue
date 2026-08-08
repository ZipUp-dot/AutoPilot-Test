<template>
  <div class="empty-state">
    <el-icon :size="60" color="#c0c4cc"><component :is="icon" /></el-icon>
    <p class="empty-text">{{ description }}</p>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, default: 'Picture' },
  description: { type: String, default: '暂无数据' },
})
</script>

<style scoped>
.empty-state { text-align: center; padding: 60px 20px; }
.empty-text { color: var(--text-secondary); margin: 12px 0; font-size: 0.9rem; }
</style>
