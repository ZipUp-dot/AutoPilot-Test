<template>
  <div v-loading="loading">
    <div class="page-header">
      <h2>报告中心</h2>
    </div>

    <!-- Filter Bar -->
    <el-card class="filter-bar">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">项目</span>
          <el-select
            v-model="filters.projectId"
            placeholder="全部项目"
            clearable
            style="width: 200px"
            @change="handleFilterChange"
          >
            <el-option
              v-for="p in projectStore.projects"
              :key="p.id"
              :label="p.name"
              :value="p.id"
            />
          </el-select>
        </div>

        <div class="filter-item">
          <span class="filter-label">时间范围</span>
          <div class="time-presets">
            <el-button
              :type="filters.timePreset === '7d' ? 'primary' : ''"
              size="small"
              @click="setTimePreset('7d')"
            >7天</el-button>
            <el-button
              :type="filters.timePreset === '30d' ? 'primary' : ''"
              size="small"
              @click="setTimePreset('30d')"
            >30天</el-button>
            <el-button
              :type="filters.timePreset === 'all' ? 'primary' : ''"
              size="small"
              @click="setTimePreset('all')"
            >全部</el-button>
            <el-date-picker
              v-model="filters.dateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              size="small"
              style="width: 240px"
              @change="onCustomDateChange"
            />
          </div>
        </div>

        <div class="filter-item">
          <span class="filter-label">排序</span>
          <el-select
            v-model="filters.sortOrder"
            style="width: 140px"
            @change="handleFilterChange"
          >
            <el-option label="通过率从高到低" value="desc" />
            <el-option label="通过率从低到高" value="asc" />
          </el-select>
        </div>
      </div>
    </el-card>

    <!-- Report Cards Grid -->
    <div v-if="filteredReports.length > 0" class="report-grid">
      <el-row :gutter="16">
        <el-col
          v-for="item in filteredReports"
          :key="item.executionId"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
        >
          <el-card class="report-card" shadow="hover">
            <div class="card-header">
              <span class="batch-name" :title="item.batchName">{{ item.batchName }}</span>
              <el-tag size="small" type="info">{{ item.projectName }}</el-tag>
            </div>

            <div class="card-body">
              <div class="pass-rate-ring">
                <span class="pass-rate-value" :style="{ color: passRateColor(item.passRate) }">
                  {{ item.passRate }}%
                </span>
                <span class="pass-rate-label">通过率</span>
              </div>
              <div class="card-meta">
                <div class="meta-row">
                  <span class="meta-label">用例数</span>
                  <span class="meta-value">{{ item.totalCases }}</span>
                </div>
                <div class="meta-row">
                  <span class="meta-label">通过</span>
                  <span class="meta-value passed">{{ item.passed }}</span>
                </div>
                <div class="meta-row">
                  <span class="meta-label">失败</span>
                  <span class="meta-value failed">{{ item.failed }}</span>
                </div>
              </div>
            </div>

            <div class="card-time">
              {{ formatTime(item.createdAt) }}
            </div>

            <div class="card-actions">
              <el-button
                size="small"
                type="primary"
                :disabled="!item.downloadUrl"
                @click="openViewer(item)"
              >
                查看
              </el-button>
              <el-button
                size="small"
                :disabled="!item.downloadUrl"
                @click="handleDownload(item)"
              >
                下载
              </el-button>
              <el-button
                size="small"
                type="danger"
                @click="handleDelete(item)"
              >
                删除
              </el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <EmptyState v-else-if="!loading" description="暂无报告数据" />

    <!-- Iframe Viewer Dialog -->
    <el-dialog
      v-model="viewerVisible"
      title="报告查看"
      fullscreen
      destroy-on-close
      class="report-viewer-dialog"
    >
      <template #header="{ close }">
        <div class="viewer-toolbar">
          <span class="viewer-title">{{ viewerReport?.batchName || '报告' }}</span>
          <div class="viewer-toolbar-actions">
            <el-button
              type="primary"
              size="small"
              :disabled="!viewerReport?.downloadUrl"
              @click="handleDownload(viewerReport)"
            >
              下载
            </el-button>
            <el-button size="small" @click="handlePrint">打印</el-button>
            <el-button size="small" @click="close">关闭</el-button>
          </div>
        </div>
      </template>
      <div class="iframe-wrapper">
        <iframe
          v-if="viewerReport?.downloadUrl"
          :src="viewerReport.downloadUrl"
          class="report-iframe"
        />
        <EmptyState v-else description="暂无报告内容" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { reportApi } from '@/api/report'
import { executionApi } from '@/api/execution'
import { useProjectStore } from '@/stores/projectStore'
import { useReportStore } from '@/stores/reportStore'
import EmptyState from '@/components/EmptyState.vue'

const projectStore = useProjectStore()
const reportStore = useReportStore()

const loading = ref(false)
const allReports = ref([])

const filters = ref({
  projectId: null,
  timePreset: 'all',
  dateRange: null,
  sortOrder: 'desc',
})

const viewerVisible = ref(false)
const viewerReport = ref(null)

const filteredReports = computed(() => {
  let list = [...allReports.value]

  // filter by project
  if (filters.value.projectId) {
    list = list.filter(r => r.projectId === filters.value.projectId)
  }

  // filter by time
  const now = Date.now()
  if (filters.value.timePreset === '7d') {
    const cutoff = now - 7 * 24 * 60 * 60 * 1000
    list = list.filter(r => new Date(r.createdAt).getTime() >= cutoff)
  } else if (filters.value.timePreset === '30d') {
    const cutoff = now - 30 * 24 * 60 * 60 * 1000
    list = list.filter(r => new Date(r.createdAt).getTime() >= cutoff)
  } else if (filters.value.dateRange && filters.value.dateRange.length === 2) {
    const [start, end] = filters.value.dateRange
    const endOfDay = new Date(end.getTime() + 24 * 60 * 60 * 1000 - 1)
    list = list.filter(r => {
      const t = new Date(r.createdAt).getTime()
      return t >= start.getTime() && t <= endOfDay.getTime()
    })
  }

  // sort by pass rate
  list.sort((a, b) => {
    return filters.value.sortOrder === 'desc'
      ? b.passRate - a.passRate
      : a.passRate - b.passRate
  })

  return list
})

function passRateColor(rate) {
  if (rate >= 90) return 'var(--success)'
  if (rate >= 60) return 'var(--warning)'
  return 'var(--danger)'
}

function setTimePreset(preset) {
  filters.value.timePreset = preset
  if (preset !== 'custom') {
    filters.value.dateRange = null
  }
  handleFilterChange()
}

function onCustomDateChange() {
  if (filters.value.dateRange) {
    filters.value.timePreset = ''
  }
  handleFilterChange()
}

function handleFilterChange() {
  // triggers computed re-evaluation
}

function formatTime(val) {
  if (!val) return '-'
  if (typeof val === 'string') {
    return val.replace('T', ' ').substring(0, 19)
  }
  return new Date(val).toLocaleString('zh-CN')
}

async function fetchAllData() {
  loading.value = true
  try {
    const projects = projectStore.projects
    const results = []

    for (const project of projects) {
      try {
        const res = await executionApi.list(project.id)
        const executions = res.data || []

        for (const exec of executions) {
          if (exec.status !== 'completed') continue

          let reportInfo = null
          try {
            const reportRes = await reportApi.getInfo(exec.id)
            reportInfo = reportRes.data
          } catch {
            // 报告未生成，仍然展示卡片
          }

          results.push({
            executionId: exec.id,
            projectId: project.id,
            projectName: project.name,
            batchName: exec.batch_name || exec.name || `执行 #${exec.id}`,
            createdAt: exec.created_at || exec.start_time,
            totalCases: exec.total_cases ?? 0,
            passed: exec.passed ?? 0,
            failed: exec.failed ?? 0,
            passRate: exec.total_cases
              ? (((exec.passed ?? 0) / exec.total_cases) * 100).toFixed(1)
              : '0.0',
            downloadUrl: reportInfo?.download_url || null,
          })
        }
      } catch {
        // skip projects with no executions
      }
    }

    allReports.value = results
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await projectStore.fetchProjects()
  await fetchAllData()
})

function openViewer(item) {
  viewerReport.value = item
  viewerVisible.value = true
}

function handleDownload(item) {
  if (item.downloadUrl) {
    window.open(item.downloadUrl, '_blank')
  }
}

function handlePrint() {
  const iframe = document.querySelector('.report-iframe')
  if (iframe) {
    iframe.contentWindow?.print()
  } else {
    ElMessage.warning('无可打印内容')
  }
}

async function handleDelete(item) {
  try {
    await ElMessageBox.confirm('确认删除该报告？删除后需重新生成。', '提示', {
      type: 'warning',
    })
    allReports.value = allReports.value.filter(r => r.executionId !== item.executionId)
    ElMessage.success('报告已删除')
  } catch {
    // cancelled
  }
}
</script>

<style scoped>
.filter-bar {
  margin-bottom: 20px;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  align-items: center;
}

.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-label {
  font-size: 0.85rem;
  color: var(--text-regular);
  white-space: nowrap;
}

.time-presets {
  display: flex;
  align-items: center;
  gap: 4px;
}

.report-grid {
  min-height: 200px;
}

.report-card {
  margin-bottom: 16px;
  transition: box-shadow 0.2s;
}

.report-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}

.batch-name {
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.card-body {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 12px;
}

.pass-rate-ring {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 64px;
}

.pass-rate-value {
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1.2;
}

.pass-rate-label {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.card-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  font-size: 0.82rem;
}

.meta-label {
  color: var(--text-secondary);
}

.meta-value {
  color: var(--text-primary);
  font-weight: 500;
}

.meta-value.passed {
  color: var(--success);
}

.meta-value.failed {
  color: var(--danger);
}

.card-time {
  font-size: 0.78rem;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.card-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  border-top: 1px solid var(--border-color);
  padding-top: 10px;
}

/* Viewer Dialog */
.viewer-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.viewer-title {
  font-size: 1rem;
  font-weight: 600;
}

.viewer-toolbar-actions {
  display: flex;
  gap: 8px;
}

.iframe-wrapper {
  width: 100%;
  height: calc(100vh - 60px);
}

.report-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
</style>
