<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2>{{ project?.name || '项目详情' }}</h2>
        <div class="project-meta">
          <el-tag size="small" effect="plain">{{ project?.target_url }}</el-tag>
          <span v-if="project?.browser_type" style="margin-left:8px;color:var(--text-secondary);font-size:.82rem">{{ project.browser_type }}</span>
          <el-tag v-if="project?.headless !== undefined" size="small" style="margin-left:8px" :type="project.headless ? 'info' : 'warning'">
            {{ project.headless ? 'Headless' : 'Headed' }}
          </el-tag>
        </div>
      </div>
    </div>

    <el-tabs :model-value="activeTab" @update:model-value="onTabChange" type="border-card">
      <el-tab-pane label="元素抓取" name="elements" />
      <el-tab-pane label="用例管理" name="cases" />
      <el-tab-pane label="执行面板" name="executions" />
      <el-tab-pane label="报告查看" name="reports" />
    </el-tabs>

    <router-view />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/projectStore'

const route = useRoute()
const router = useRouter()
const pid = computed(() => Number(route.params.id))
const store = useProjectStore()
const project = computed(() => store.current)
const loading = ref(false)

const activeTab = ref('elements')

onMounted(async () => {
  loading.value = true
  await store.fetchProjects()
  const found = store.projects.find(p => p.id === pid.value)
  if (found) store.setCurrentProject(found)
  else await loadProject()
  loading.value = false
  syncTabFromRoute()
})

async function loadProject() {
  const res = await store.fetchProjects()
  const found = (store.projects || []).find(p => p.id === pid.value)
  if (found) store.setCurrentProject(found)
}

function syncTabFromRoute() {
  const path = route.path
  if (path.endsWith('/cases')) activeTab.value = 'cases'
  else if (path.endsWith('/executions')) activeTab.value = 'executions'
  else if (path.endsWith('/reports')) activeTab.value = 'reports'
  else activeTab.value = 'elements'
}

function onTabChange(tab) {
  activeTab.value = tab
  router.push(`/projects/${pid.value}/${tab}`)
}
</script>

<style scoped>
.project-meta { display: flex; align-items: center; gap: 4px; margin-top: 4px; }
</style>
