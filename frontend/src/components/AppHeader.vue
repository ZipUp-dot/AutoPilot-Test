<template>
  <div class="app-header">
    <div class="header-left">
      <h1 class="logo">AutoPilot</h1>
      <span class="tagline">AI 驱动自动化测试</span>
    </div>
    <div class="header-right">
      <el-button link type="primary" @click="router.push('/projects')">项目管理</el-button>
      <el-button link type="primary" @click="router.push('/reports')">报告中心</el-button>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<style scoped>
.app-header { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.header-left { display: flex; align-items: center; gap: 12px; }
.logo { font-size: 1.15rem; font-weight: 700; color: var(--primary); }
.tagline { font-size: 0.82rem; color: var(--text-secondary); }
.header-right { display: flex; gap: 8px; }
</style>
