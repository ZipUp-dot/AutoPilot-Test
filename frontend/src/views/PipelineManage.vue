<template>
  <div class="pipeline-manage">
    <div class="page-header">
      <h2>CI/CD 流水线</h2>
      <el-space>
        <el-select v-model="projectId" placeholder="选择项目" style="width: 220px" @change="reload">
          <el-option v-for="p in projectStore.projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-button :disabled="!projectId" @click="reload">
          <el-icon style="margin-right:4px"><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="primary" :disabled="!projectId" @click="openCreate">
          <el-icon style="margin-right:4px"><Plus /></el-icon>新建流水线
        </el-button>
      </el-space>
    </div>

    <el-table :data="store.items" v-loading="store.loading" border>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="阶段" min-width="200">
        <template #default="{ row }">
          <el-tag v-for="s in row.stages_json" :key="s.name" size="small" style="margin-right:4px">
            {{ s.name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="触发方式" min-width="160">
        <template #default="{ row }">
          <span v-if="row.trigger_config_json?.manual">手动 </span>
          <span v-if="row.trigger_config_json?.schedule_id">调度#{{ row.trigger_config_json.schedule_id }} </span>
          <span v-if="row.trigger_config_json?.webhook_token">Webhook</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="100">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="trigger(row)">触发</el-button>
          <el-button size="small" @click="goRuns(row)">运行</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-alert
      type="info" :closable="false" show-icon style="margin-top:16px"
      title="触发只构造执行请求，Execution 由 ExecutionAdmission 创建；run 状态由下属 executions 终态派生。"
    />

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑流水线' : '新建流水线'" width="680px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="阶段定义" required>
          <div class="stages-editor">
            <div v-for="(st, i) in form.stages" :key="i" class="stage-row">
              <el-input v-model="st.name" placeholder="阶段名" style="width:150px" />
              <el-input v-model="st.caseIdsText" placeholder="用例 ID，逗号分隔" style="width:240px" />
              <el-input v-model="st.env" placeholder="环境（可选）" style="width:140px" />
              <el-button size="small" type="danger" :disabled="form.stages.length<=1" @click="form.stages.splice(i,1)">删</el-button>
            </div>
            <el-button size="small" @click="form.stages.push({ name: '', caseIdsText: '', env: '' })">+ 添加阶段</el-button>
          </div>
        </el-form-item>
        <el-form-item label="Webhook Token">
          <el-input v-model="form.webhook_token" placeholder="留空则不开放 webhook 触发" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item v-if="editing?.id" label="Webhook URL">
          <el-input :model-value="webhookUrl(editing.id)" readonly>
            <template #append>
              <el-button @click="copy(webhookUrl(editing.id))">复制</el-button>
            </template>
          </el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/projectStore'
import { usePipelineStore } from '@/stores/pipelines'

const store = usePipelineStore()
const projectStore = useProjectStore()
const router = useRouter()

const projectId = ref(null)
const dialogVisible = ref(false)
const editing = ref(null)
const form = reactive({ name: '', stages: [{ name: '', caseIdsText: '', env: '' }], webhook_token: '', enabled: true })

function webhookUrl(id) {
  return `${window.location.origin}/api/v1/pipelines/${id}/trigger`
}

function reload() {
  if (projectId.value) store.fetchList(projectId.value)
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', stages: [{ name: '', caseIdsText: '', env: '' }], webhook_token: '', enabled: true })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    stages: (row.stages_json || []).map(s => ({
      name: s.name,
      caseIdsText: (s.case_selector?.case_ids || []).join(','),
      env: s.env || '',
    })),
    webhook_token: row.trigger_config_json?.webhook_token || '',
    enabled: row.enabled,
  })
  dialogVisible.value = true
}

function payload() {
  return {
    name: form.name,
    stages_json: form.stages.map(s => ({
      name: s.name,
      case_selector: {
        case_ids: s.caseIdsText.split(',').map(x => Number(x.trim())).filter(n => Number.isInteger(n) && n > 0),
      },
      env: s.env || null,
    })),
    trigger_config_json: {
      manual: true,
      webhook_token: form.webhook_token || null,
    },
    enabled: form.enabled,
  }
}

async function submit() {
  if (!form.name.trim()) return ElMessage.warning('请填写名称')
  const body = payload()
  if (body.stages_json.some(s => !s.name)) return ElMessage.warning('阶段名不能为空')
  if (editing.value) {
    await store.update(editing.value.id, body, projectId.value)
  } else {
    await store.create(projectId.value, body)
  }
  dialogVisible.value = false
  ElMessage.success('已保存')
}

async function trigger(row) {
  const data = await store.trigger(row.id, { trigger_type: 'manual' })
  ElMessage.success(`已触发，run #${data?.run_id ?? '—'}`)
  router.push(`/pipelines/${row.id}/runs`)
}

async function remove(row) {
  await ElMessageBox.confirm(`确认删除流水线「${row.name}」？`, '删除', {
    confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
  })
  await store.remove(row.id, projectId.value)
  ElMessage.success('已删除')
}

function goRuns(row) {
  router.push(`/pipelines/${row.id}/runs`)
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch (e) {
    ElMessage.warning('复制失败，请手动选择')
  }
}

onMounted(async () => {
  if (!projectStore.projects.length) await projectStore.fetchProjects?.()
  if (projectStore.projects.length) {
    projectId.value = projectStore.projects[0].id
    reload()
  }
})
</script>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.stages-editor { width: 100%; }
.stage-row { display: flex; gap: 8px; margin-bottom: 8px; }
</style>
