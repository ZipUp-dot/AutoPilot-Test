<template>
  <el-container class="app-container">
    <el-aside :width="collapsed ? '64px' : '220px'" class="app-sidebar">
      <AppSidebar :collapsed="collapsed" @toggle="collapsed = !collapsed" />
    </el-aside>
    <el-container>
      <el-header class="app-header">
        <div class="mobile-toggle" @click="collapsed = !collapsed">
          <el-icon :size="20"><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
        </div>
        <AppHeader />
      </el-header>
      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <keep-alive include="ProjectDetail">
              <component :is="Component" />
            </keep-alive>
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import AppSidebar from '@/components/AppSidebar.vue'

const collapsed = ref(false)
</script>

<style scoped>
.app-container { height: 100vh; overflow: hidden; }
.app-sidebar { background: var(--sidebar-bg); transition: width 0.3s; overflow: hidden; }
.app-header { height: 56px; padding: 0 20px; background: #fff; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; gap: 12px; }
.app-main { background: var(--page-bg); padding: 20px; overflow-y: auto; height: calc(100vh - 56px); }
.mobile-toggle { display: none; cursor: pointer; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.15s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (max-width: 767px) {
  .app-main { padding: 12px; }
  .mobile-toggle { display: block; }
}
</style>
