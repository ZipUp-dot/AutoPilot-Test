<template>
  <div>
    <div class="page-header">
      <h2>AutoPilot 仪表盘</h2>
      <el-space>
        <el-button type="primary" @click="router.push('/projects')">项目管理</el-button>
        <el-button @click="router.push('/reports')">报告中心</el-button>
      </el-space>
    </div>

    <div class="stat-cards">
      <el-card shadow="hover" class="stat-item"><div class="stat-num" style="color:#409eff">{{ stats.projects }}</div><div class="stat-label">已执行项目</div></el-card>
      <el-card shadow="hover" class="stat-item"><div class="stat-num" style="color:#67c23a">{{ stats.cases }}</div><div class="stat-label">用例总数</div></el-card>
      <el-card shadow="hover" class="stat-item"><div class="stat-num" style="color:#e6a23c">{{ stats.executions }}</div><div class="stat-label">执行次数</div></el-card>
      <el-card shadow="hover" class="stat-item"><div class="stat-num" style="color:#f56c6c">{{ stats.avgRate }}%</div><div class="stat-label">平均通过率</div></el-card>
    </div>

    <el-card v-if="recentExecutions.length" style="margin-top:20px">
      <template #header><span>最近执行记录</span></template>
      <el-table :data="recentExecutions" size="small" @row-click="(row) => router.push(`/executions/${row.id}`)" style="cursor:pointer">
        <el-table-column prop="batch_name" label="批次名称" />
        <el-table-column prop="project_name" label="项目" />
        <el-table-column label="平台" width="80"><template #default="{row}"><el-tag :type="row.platform === 'android' ? 'success' : 'info'" size="small">{{ row.platform === 'android' ? 'Android' : 'Web' }}</el-tag></template></el-table-column>
        <el-table-column label="模式" width="90"><template #default="{row}"><el-tag size="small">{{ row.execution_mode }}</el-tag></template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="{row}"><ExecutionStatusTag :status="row.status" /></template></el-table-column>
        <el-table-column label="通过" width="60"><template #default="{row}">{{ row.passed_cases }}/{{ row.total_cases }}</template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/projectStore'
import { executionApi } from '@/api/execution'
import { caseApi } from '@/api/case'
import ExecutionStatusTag from '@/components/ExecutionStatusTag.vue'

const router = useRouter()
const projectStore = useProjectStore()
const stats = ref({ projects: 0, cases: 0, executions: 0, avgRate: 0 })
const recentExecutions = ref([])

onMounted(async () => {
  try {
    await projectStore.fetchProjects()

    let executedProjects = 0, totalCases = 0, totalExec = 0, totalPassed = 0, totalCasesAll = 0
    const allExecs = []
    // 已结束且有结果的执行状态（completed/failed/stopped/interrupted）。
    // queued/running/healing 尚未产出结果，不参与通过率统计。
    const FINISHED = new Set(['completed', 'failed', 'stopped', 'interrupted'])

    for (const p of projectStore.projects) {
      try {
        const casesRes = await caseApi.list(p.id, 1, 1)
        totalCases += casesRes.data?.total || 0

        const execRes = await executionApi.list(p.id)
        const execs = execRes.data?.items || []
        allExecs.push(...execs.map(e => ({ ...e, project_name: p.name })))
        totalExec += execs.length

        // 项目总数 = 有执行记录的项目数（与下方「最近执行记录」对齐）
        if (execs.length > 0) executedProjects++

        execs.forEach(e => {
          // 平均通过率 = 已结束执行通过的用例合计 / 已结束执行总用例合计，
          // 与下方「通过 x/y」各列合计一致（含 interrupted/failed，不再只算 completed）。
          if (FINISHED.has(e.status) && e.total_cases > 0) {
            totalPassed += e.passed_cases
            totalCasesAll += e.total_cases
          }
        })
      } catch (e) { /* skip */ }
    }

    stats.value.projects = executedProjects
    stats.value.cases = totalCases
    stats.value.executions = totalExec
    stats.value.avgRate = totalCasesAll > 0 ? Math.round((totalPassed / totalCasesAll) * 100) : 0
    // 跨项目按开始时间倒序，保证「最近执行记录」确实是最近的在前
    recentExecutions.value = allExecs
      .sort((a, b) => (b.start_time || '').localeCompare(a.start_time || ''))
      .slice(0, 10)
  } catch (e) { /* ignore */ }
})
</script>

<style scoped>
.stat-item { text-align: center; padding: 8px; }
.stat-num { font-size: 1.8rem; font-weight: 700; }
.stat-label { font-size: 0.82rem; color: var(--text-secondary); margin-top: 4px; }
</style>
