<template>
  <div>
    <div class="page-header">
      <h2>项目管理</h2>
      <el-button type="primary" @click="openCreate">新建项目</el-button>
    </div>

    <el-table :data="projectStore.projects" v-loading="projectStore.loading" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="项目名称" />
      <el-table-column prop="target_url" label="目标地址" show-overflow-tooltip />
      <el-table-column prop="platform" label="平台" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="row.platform === 'android' ? 'success' : 'info'" size="small">
            {{ row.platform === 'android' ? 'Android' : 'Web' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push(`/projects/${row.id}`)">详情</el-button>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" :title="editingId ? '编辑项目' : '新建项目'" width="640px" @closed="resetForm">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="项目名称" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="目标地址" prop="target_url">
          <el-input v-model="form.target_url" placeholder="https://example.com" :disabled="editingId && form.platform === 'android'" />
        </el-form-item>
        <el-form-item label="平台" prop="platform">
          <el-radio-group v-model="form.platform" :disabled="!!editingId">
            <el-radio value="web">Web</el-radio>
            <el-radio value="android">Android</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- Android 配置 -->
        <template v-if="form.platform === 'android'">
          <el-divider content-position="left">Android 配置</el-divider>
          <el-form-item label="Appium 地址">
            <el-input v-model="form.androidConfig.appium_server_url" placeholder="http://localhost:4723" />
          </el-form-item>
          <el-form-item label="App Package">
            <el-input v-model="form.androidConfig.app_package" placeholder="com.example.app" />
          </el-form-item>
          <el-form-item label="App Activity">
            <el-input v-model="form.androidConfig.app_activity" placeholder=".MainActivity" />
          </el-form-item>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="设备名称">
                <el-input v-model="form.androidConfig.device_name" placeholder="emulator-5554" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="平台版本">
                <el-input v-model="form.androidConfig.platform_version" placeholder="12.0" />
              </el-form-item>
            </el-col>
          </el-row>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/projectStore'
import { ElMessage, ElMessageBox } from 'element-plus'

const router = useRouter()
const projectStore = useProjectStore()
const showCreate = ref(false)
const editingId = ref(null)
const formRef = ref(null)

const URL_PATTERN = /^https?:\/\/.+/

const rules = {
  name: [
    { required: true, message: '名称不能为空', trigger: 'blur' },
    { max: 100, message: '名称不能超过100个字符', trigger: 'blur' },
  ],
  target_url: [
    { required: true, message: '目标地址不能为空', trigger: 'blur' },
    { pattern: URL_PATTERN, message: '请输入合法的 URL 格式（http:// 或 https://）', trigger: 'blur' },
  ],
  platform: [
    { required: true, message: '请选择平台', trigger: 'change' },
  ],
}

const defaultAndroidConfig = {
  appium_server_url: '',
  app_package: '',
  app_activity: '',
  device_name: '',
  platform_version: '',
  automation_engine: 'uiautomator2',
}

const form = reactive({
  name: '',
  target_url: '',
  platform: 'web',
  androidConfig: { ...defaultAndroidConfig },
})

function resetForm() {
  editingId.value = null
  form.name = ''
  form.target_url = ''
  form.platform = 'web'
  form.androidConfig = { ...defaultAndroidConfig }
  formRef.value?.resetFields()
}

onMounted(() => projectStore.fetchProjects())

function openCreate() {
  resetForm()
  showCreate.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.name = row.name
  form.target_url = row.target_url
  form.platform = row.platform || 'web'
  const config = row.config_json || {}
  form.androidConfig = {
    appium_server_url: config.appium_server_url || '',
    app_package: config.app_package || '',
    app_activity: config.app_activity || '',
    device_name: config.device_name || '',
    platform_version: config.platform_version || '',
    automation_engine: config.automation_engine || 'uiautomator2',
  }
  showCreate.value = true
}

function buildSubmitData() {
  const data = {
    name: form.name,
    target_url: form.target_url,
    platform: form.platform,
  }
  if (form.platform === 'android') {
    const config = { ...form.androidConfig }
    // 移除空值
    Object.keys(config).forEach(k => { if (!config[k]) delete config[k] })
    data.config_json = config
  }
  return data
}

async function handleSubmit() {
  await formRef.value.validate()
  const data = buildSubmitData()
  if (editingId.value) {
    await projectStore.updateProject(editingId.value, data)
    ElMessage.success('更新成功')
  } else {
    await projectStore.createProject(data)
    ElMessage.success('创建成功')
  }
  showCreate.value = false
  resetForm()
  projectStore.fetchProjects()
}

async function handleDelete(id) {
  await ElMessageBox.confirm('确认删除该项目？所有关联数据将被清空。')
  await projectStore.deleteProject(id)
  ElMessage.success('已删除')
  projectStore.fetchProjects()
}
</script>