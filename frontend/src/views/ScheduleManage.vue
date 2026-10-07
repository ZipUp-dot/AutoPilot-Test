<template>
  <div class="schedule-manage">
    <div class="page-header">
      <h2>定时执行</h2>
      <el-space>
        <el-select
          v-model="projectId"
          placeholder="选择项目"
          style="width: 220px"
          @change="reload"
        >
          <el-option
            v-for="p in projectStore.projects"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          />
        </el-select>
        <el-button :disabled="!projectId" @click="reload">
          <el-icon style="margin-right:4px"><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" :disabled="!projectId" @click="openCreate">
          <el-icon style="margin-right:4px"><Plus /></el-icon>
          新建定时任务
        </el-button>
      </el-space>
    </div>

    <el-table :data="store.items" v-loading="store.loading" border>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="cron" min-width="200">
        <template #default="{ row }">
          <div class="mono">{{ row.cron_expr }}</div>
          <div class="cron-human">{{ describeCron(row.cron_expr) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="下次运行" min-width="170">
        <template #default="{ row }">{{ fmt(row.next_run_at) }}</template>
      </el-table-column>
      <el-table-column label="上次运行" min-width="170">
        <template #default="{ row }">{{ fmt(row.last_run_at) }}</template>
      </el-table-column>
      <el-table-column label="上次执行" width="110">
        <template #default="{ row }">
          <el-link
            v-if="row.last_execution_id"
            type="primary"
            @click="goExecution(row.last_execution_id)"
          >
            #{{ row.last_execution_id }}
          </el-link>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="110">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            @change="(v) => toggle(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="triggerOnce(row)">手动触发</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-top:16px"
      title="「停止调度」只关闭定时器，不影响正在运行的执行实例；停止执行请到执行详情页操作。"
    />

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑定时任务' : '新建定时任务'" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" placeholder="如 每日 9 点回归" />
        </el-form-item>
        <el-form-item label="cron 表达式" required>
          <el-input v-model="form.cron_expr" placeholder="5 段：分 时 日 月 周，如 0 9 * * 1-5" />
          <div class="cron-human">{{ describeCron(form.cron_expr) }}</div>
        </el-form-item>
        <el-form-item label="用例 ID" required>
          <el-input v-model="form.caseIdsText" placeholder="逗号分隔，如 1,2,3" />
        </el-form-item>
        <el-form-item label="执行模式">
          <el-select v-model="form.mode" style="width:160px">
            <el-option label="headless" value="headless" />
            <el-option label="headed" value="headed" />
          </el-select>
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
import { useScheduleStore, describeCron } from '@/stores/schedules'

const store = useScheduleStore()
const projectStore = useProjectStore()
const router = useRouter()

const projectId = ref(null)
const dialogVisible = ref(false)
const editing = ref(null)
const form = reactive({ name: '', cron_expr: '0 9 * * 1-5', caseIdsText: '', mode: 'headless' })

function fmt(v) {
  if (!v) return '—'
  return String(v).replace('T', ' ').slice(0, 19)
}

function reload() {
  if (!projectId.value) return
  store.fetchList(projectId.value)
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', cron_expr: '0 9 * * 1-5', caseIdsText: '', mode: 'headless' })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  let cfg = row.exec_config_json || {}
  Object.assign(form, {
    name: row.name,
    cron_expr: row.cron_expr,
    caseIdsText: (cfg.case_ids || []).join(','),
    mode: cfg.mode || 'headless',
  })
  dialogVisible.value = true
}

function payload() {
  const case_ids = form.caseIdsText
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0)
  return {
    name: form.name,
    cron_expr: form.cron_expr,
    exec_config_json: { case_ids, mode: form.mode },
  }
}

async function submit() {
  if (!form.name.trim()) return ElMessage.warning('请填写名称')
  const body = payload()
  if (!body.exec_config_json.case_ids.length) return ElMessage.warning('请填写至少一个用例 ID')
  if (editing.value) {
    await store.update(editing.value.id, body, projectId.value)
  } else {
    await store.create(projectId.value, body)
  }
  dialogVisible.value = false
  ElMessage.success('已保存')
}

async function toggle(row, next) {
  if (next) {
    await store.enable(row.id, projectId.value)
    ElMessage.success('已启用')
  } else {
    await ElMessageBox.confirm('停止调度只关闭定时器，不影响正在运行的执行实例。确认停止？', '停止调度', {
      confirmButtonText: '确认停止',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await store.disable(row.id, projectId.value)
    ElMessage.success('已停止调度')
  }
}

async function triggerOnce(row) {
  const data = await store.trigger(row.id)
  ElMessage.success(`已触发，执行 ID：${data?.execution_id ?? '—'}`)
  reload()
}

async function remove(row) {
  await ElMessageBox.confirm(`确认删除定时任务「${row.name}」？`, '删除', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
  await store.remove(row.id, projectId.value)
  ElMessage.success('已删除')
}

function goExecution(id) {
  router.push(`/executions/${id}`)
}

onMounted(async () => {
  if (!projectStore.projects.length) {
    await projectStore.fetchProjects?.()
  }
  if (projectStore.projects.length) {
    projectId.value = projectStore.projects[0].id
    reload()
  }
})
</script>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.mono { font-family: monospace; }
.cron-human { color: #909399; font-size: 12px; margin-top: 2px; }
</style>
