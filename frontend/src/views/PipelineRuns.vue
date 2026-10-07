<template>
  <div class="pipeline-runs">
    <div class="page-header">
      <h2>流水线运行</h2>
      <el-space>
        <el-button @click="$router.push('/pipelines')">
          <el-icon style="margin-right:4px"><Back /></el-icon>返回
        </el-button>
        <el-button type="primary" @click="reload">
          <el-icon style="margin-right:4px"><Refresh /></el-icon>刷新
        </el-button>
      </el-space>
    </div>

    <el-table :data="store.runs" v-loading="store.loading" border @row-click="selectRun">
      <el-table-column prop="id" label="Run" width="90">
        <template #default="{ row }">#{{ row.id }}</template>
      </el-table-column>
      <el-table-column prop="trigger_type" label="触发方式" width="120" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="started_at" label="开始" min-width="180" />
      <el-table-column prop="finished_at" label="结束" min-width="180" />
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-button size="small" @click.stop="selectRun(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>

    <template v-if="store.runDetail">
      <h3 style="margin-top:24px">Run #{{ store.runDetail.id }} · 阶段明细</h3>
      <el-table :data="store.runDetail.stages" border>
        <el-table-column prop="name" label="阶段" min-width="140" />
        <el-table-column label="Execution" width="120">
          <template #default="{ row }">
            <el-link v-if="row.execution_id" type="primary" @click="goExecution(row.execution_id)">
              #{{ row.execution_id }}
            </el-link>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.status" :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="通过 / 失败 / 跳过" min-width="180">
          <template #default="{ row }">
            {{ row.passed_cases }} / {{ row.failed_cases }} / {{ row.skipped }}
          </template>
        </el-table-column>
        <el-table-column label="通过率" width="110">
          <template #default="{ row }">
            <el-tag :type="passRateType(row)" size="small">{{ passRate(row) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-alert
        type="info" :closable="false" show-icon style="margin-top:12px"
        title="阶段通过率与 /executions 接口同口径（CaseStateResolver 真值表）；前端只渲染不派生。"
      />
    </template>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePipelineStore } from '@/stores/pipelines'

const store = usePipelineStore()
const route = useRoute()
const router = useRouter()

const pipelineId = Number(route.params.id)

function statusType(s) {
  if (s === 'success' || s === 'completed') return 'success'
  if (s === 'failed' || s === 'stopped' || s === 'interrupted') return 'danger'
  if (s === 'running') return 'warning'
  return 'info'
}

function total(row) {
  return (row.passed_cases || 0) + (row.failed_cases || 0) + (row.skipped || 0)
}

function passRate(row) {
  const t = total(row)
  if (!t) return '—'
  return `${Math.round((row.passed_cases / t) * 100)}%`
}

function passRateType(row) {
  const t = total(row)
  if (!t) return 'info'
  const pct = row.passed_cases / t
  if (pct >= 1) return 'success'
  if (pct >= 0.8) return 'warning'
  return 'danger'
}

async function reload() {
  await store.fetchRuns(pipelineId)
  if (store.runs.length) await store.fetchRunDetail(store.runs[0].id)
  else store.runDetail = null
}

function selectRun(row) {
  store.fetchRunDetail(row.id)
}

function goExecution(id) {
  router.push(`/executions/${id}`)
}

onMounted(reload)
</script>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
</style>
