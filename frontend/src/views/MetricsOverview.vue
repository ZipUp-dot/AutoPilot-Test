<template>
  <div class="metrics-overview">
    <div class="page-header">
      <h2>KPI 指标</h2>
      <el-space>
        <el-select
          v-model="projectId"
          placeholder="全项目口径"
          clearable
          style="width: 200px"
          @change="reload"
        >
          <el-option
            v-for="p in projectStore.projects"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          />
        </el-select>
        <el-button @click="reload">
          <el-icon style="margin-right:4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </el-space>
    </div>

    <div class="kpi-cards" v-loading="store.loading">
      <el-card v-for="c in cards" :key="c.key" shadow="hover" class="kpi-item">
        <div class="kpi-title">{{ c.title }}</div>
        <div class="kpi-num" :style="{ color: c.color }">{{ c.value }}</div>
        <div class="kpi-detail">{{ c.detail }}</div>
        <div class="kpi-path mono">{{ c.path }}</div>
      </el-card>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="数据来源：后端 GET /api/v1/metrics 只读聚合；本页只渲染，不做任何派生计算。"
      style="margin-top:16px"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useMetricsStore, formatRate, KPI_PATHS } from '@/stores/metrics'
import { useProjectStore } from '@/stores/projectStore'

const store = useMetricsStore()
const projectStore = useProjectStore()
const projectId = ref(null)

function num(v) {
  return v === null || v === undefined ? '—' : v
}

/** 直接渲染后端四路径原值（含分子/分母），不做任何计算 */
const cards = computed(() => {
  const o = store.overview || {}
  return [
    {
      key: 'first',
      title: '首生成有效率（目标 70%）',
      path: KPI_PATHS[0],
      value: formatRate(store.kpis.firstGenerationRate),
      detail: `${num(o.first_generation_success_rate?.numerator)} / ${num(o.first_generation_success_rate?.denominator)}`,
      color: '#409eff',
    },
    {
      key: 'final',
      title: '最终成功率（目标 85%）',
      path: KPI_PATHS[1],
      value: formatRate(store.kpis.finalSuccessRate),
      detail: `${num(o.final_success_rate?.numerator)} / ${num(o.final_success_rate?.denominator)}`,
      color: '#67c23a',
    },
    {
      key: 'pipeline',
      title: 'Pipeline Coverage',
      path: KPI_PATHS[2],
      value: formatRate(store.kpis.pipelineCoverage),
      detail: `ready ${num(o.pipeline_coverage?.ready)} / total ${num(o.pipeline_coverage?.total)}`,
      color: '#e6a23c',
    },
    {
      key: 'start',
      title: 'Execution Start Coverage',
      path: KPI_PATHS[3],
      value: formatRate(store.kpis.executionStartCoverage),
      detail: `started ${num(o.execution_start_coverage?.started)} / total ${num(o.execution_start_coverage?.total)}`,
      color: '#f56c6c',
    },
  ]
})

async function reload() {
  await store.fetchOverview(projectId.value || undefined)
}

onMounted(async () => {
  try { await projectStore.fetchProjects() } catch { /* 项目列表失败不阻断 KPI */ }
  await reload()
})
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.kpi-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.kpi-item { text-align: center; }
.kpi-title { font-size: .84rem; color: var(--text-secondary); }
.kpi-num { font-size: 1.8rem; font-weight: 700; margin: 6px 0; }
.kpi-detail { font-size: .78rem; color: var(--text-secondary); }
.kpi-path { font-size: .7rem; color: var(--text-secondary); margin-top: 6px; word-break: break-all; }
.mono { font-family: ui-monospace, monospace; }
</style>
