<template>
  <div class="sidebar">
    <el-menu
      :default-active="activeRoute"
      :collapse="collapsed"
      background-color="#1d1e2c"
      text-color="#bfcbd9"
      active-text-color="#409eff"
      router
    >
      <el-menu-item index="/dashboard">
        <el-icon><Odometer /></el-icon>
        <span>仪表盘</span>
      </el-menu-item>
      <el-menu-item index="/projects">
        <el-icon><Folder /></el-icon>
        <span>项目管理</span>
      </el-menu-item>
      <el-menu-item index="/reports">
        <el-icon><Document /></el-icon>
        <span>报告中心</span>
      </el-menu-item>
      <el-menu-item index="/metrics">
        <el-icon><TrendCharts /></el-icon>
        <span>KPI 指标</span>
      </el-menu-item>
      <el-menu-item index="/schedules">
        <el-icon><Timer /></el-icon>
        <span>定时执行</span>
      </el-menu-item>
      <el-menu-item index="/pipelines">
        <el-icon><Promotion /></el-icon>
        <span>CI/CD 流水线</span>
      </el-menu-item>
    </el-menu>
    <div class="collapse-btn" @click="$emit('toggle')">
      <el-icon :size="18"><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

defineProps({ collapsed: { type: Boolean, default: false } })
defineEmits(['toggle'])

const route = useRoute()
const activeRoute = computed(() => {
  if (route.path === '/dashboard' || route.path === '/') return '/dashboard'
  if (route.path.startsWith('/projects')) return '/projects'
  if (route.path.startsWith('/reports')) return '/reports'
  if (route.path.startsWith('/metrics')) return '/metrics'
  if (route.path.startsWith('/schedules')) return '/schedules'
  if (route.path.startsWith('/pipelines')) return '/pipelines'
  return '/dashboard'
})
</script>

<style scoped>
.sidebar { height: 100%; display: flex; flex-direction: column; }
.el-menu { border-right: none; flex: 1; }
.collapse-btn { text-align: center; padding: 12px; cursor: pointer; color: var(--sidebar-text); border-top: 1px solid rgba(255,255,255,.06); }
.collapse-btn:hover { color: #fff; }
</style>
