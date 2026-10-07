<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2>{{ project?.name || '项目详情' }}</h2>
        <div class="project-meta">
          <el-tag size="small" effect="plain">{{ project?.target_url }}</el-tag>
          <el-tag :type="project?.platform === 'android' ? 'success' : 'info'" size="small" style="margin-left:8px">
            {{ project?.platform === 'android' ? 'Android' : 'Web' }}
          </el-tag>
          <span v-if="project?.browser_type" style="margin-left:8px;color:var(--text-secondary);font-size:.82rem">{{ project.browser_type }}</span>
          <el-tag v-if="project?.headless !== undefined" size="small" style="margin-left:8px" :type="project.headless ? 'info' : 'warning'">
            {{ project.headless ? 'Headless' : 'Headed' }}
          </el-tag>
          <el-button v-if="project?.platform === 'android'" size="small" style="margin-left:auto" @click="showAndroidConfig = true">
            Android 配置
          </el-button>
          <el-button v-if="project && project.platform !== 'android'" size="small" style="margin-left:auto" @click="openWebConfig">
            Web 执行配置
          </el-button>
        </div>
      </div>
    </div>

    <el-tabs :model-value="activeTab" @update:model-value="onTabChange" type="border-card">
      <el-tab-pane label="元素抓取" name="elements" />
      <el-tab-pane label="用例管理" name="cases" />
      <el-tab-pane label="AI 用例审核" name="ai-review" />
      <el-tab-pane label="执行面板" name="executions" />
      <el-tab-pane label="报告查看" name="reports" />
    </el-tabs>

    <router-view />

    <!-- Android 配置弹窗 -->
    <el-dialog v-model="showAndroidConfig" title="Android 配置" width="560px" destroy-on-close>
      <el-form :model="androidConfig" label-width="140px" v-loading="savingConfig">
        <el-form-item label="Appium 地址">
          <el-input v-model="androidConfig.appium_server_url" placeholder="http://localhost:4723" />
        </el-form-item>
        <el-form-item label="App Package">
          <el-input v-model="androidConfig.app_package" placeholder="com.example.app" />
        </el-form-item>
        <el-form-item label="App Activity">
          <el-input v-model="androidConfig.app_activity" placeholder=".MainActivity" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="设备名称">
              <el-input v-model="androidConfig.device_name" placeholder="emulator-5554" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="平台版本">
              <el-input v-model="androidConfig.platform_version" placeholder="12.0" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="自动化引擎">
          <el-input v-model="androidConfig.automation_engine" placeholder="uiautomator2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAndroidConfig = false">取消</el-button>
        <el-button type="primary" :loading="savingConfig" @click="handleSaveAndroidConfig">保存</el-button>
      </template>
    </el-dialog>

    <!-- Web 执行配置弹窗（Mock 服务，可空） -->
    <el-dialog v-model="showWebConfig" title="Web 执行配置" width="520px" destroy-on-close>
      <el-form :model="webConfig" label-width="140px" v-loading="savingWebConfig">
        <el-form-item label="Mock 服务">
          <el-select
            v-model="webConfig.mock_server_id"
            placeholder="不使用 Mock（放行真实请求）"
            clearable
            style="width:100%"
          >
            <el-option
              v-for="s in mockServers"
              :key="s.id"
              :label="`${s.name}（${s.base_path}）`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-alert
          type="info" :closable="false" show-icon
          title="Mock 仅注入 Web 执行链；Admission 时冻结进 Manifest，执行期只读快照。"
        />
      </el-form>
      <template #footer>
        <el-button @click="showWebConfig = false">取消</el-button>
        <el-button type="primary" :loading="savingWebConfig" @click="handleSaveWebConfig">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/projectStore'
import { useMockStore } from '@/stores/mock'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const pid = computed(() => Number(route.params.id))
const store = useProjectStore()
const mockStore = useMockStore()
const project = computed(() => store.current)
const loading = ref(false)

const activeTab = ref('elements')
const showAndroidConfig = ref(false)
const savingConfig = ref(false)
const androidConfig = ref({
  appium_server_url: '',
  app_package: '',
  app_activity: '',
  device_name: '',
  platform_version: '',
  automation_engine: 'uiautomator2',
})

// Web 执行配置（Mock 服务，可空）
const showWebConfig = ref(false)
const savingWebConfig = ref(false)
const mockServers = ref([])
const webConfig = ref({ mock_server_id: null })

watch(project, (p) => {
  if (p?.config_json) {
    const c = p.config_json
    androidConfig.value = {
      appium_server_url: c.appium_server_url || '',
      app_package: c.app_package || '',
      app_activity: c.app_activity || '',
      device_name: c.device_name || '',
      platform_version: c.platform_version || '',
      automation_engine: c.automation_engine || 'uiautomator2',
    }
    webConfig.value = { mock_server_id: c.mock_server_id ?? null }
  }
})

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
  else if (path.endsWith('/ai-review')) activeTab.value = 'ai-review'
  else if (path.endsWith('/executions')) activeTab.value = 'executions'
  else if (path.endsWith('/reports')) activeTab.value = 'reports'
  else activeTab.value = 'elements'
}

function onTabChange(tab) {
  activeTab.value = tab
  router.push(`/projects/${pid.value}/${tab}`)
}

async function handleSaveAndroidConfig() {
  savingConfig.value = true
  try {
    const config = { ...androidConfig.value }
    Object.keys(config).forEach(k => { if (!config[k]) delete config[k] })
    await store.updateProject(pid.value, { config_json: config })
    await store.fetchProjects()
    const found = store.projects.find(p => p.id === pid.value)
    if (found) store.setCurrentProject(found)
    showAndroidConfig.value = false
    ElMessage.success('Android 配置已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    savingConfig.value = false
  }
}

async function openWebConfig() {
  webConfig.value = { mock_server_id: project.value?.config_json?.mock_server_id ?? null }
  mockServers.value = []
  showWebConfig.value = true
  try {
    mockServers.value = await mockStore.fetchServers(pid.value)
  } catch {
    ElMessage.error('获取 Mock 服务列表失败')
  }
}

async function handleSaveWebConfig() {
  savingWebConfig.value = true
  try {
    const config = { ...(project.value?.config_json || {}) }
    if (webConfig.value.mock_server_id) config.mock_server_id = webConfig.value.mock_server_id
    else delete config.mock_server_id
    await store.updateProject(pid.value, { config_json: config })
    await store.fetchProjects()
    const found = store.projects.find(p => p.id === pid.value)
    if (found) store.setCurrentProject(found)
    showWebConfig.value = false
    ElMessage.success('Web 执行配置已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    savingWebConfig.value = false
  }
}
</script>

<style scoped>
.project-meta { display: flex; align-items: center; gap: 4px; margin-top: 4px; }
</style>