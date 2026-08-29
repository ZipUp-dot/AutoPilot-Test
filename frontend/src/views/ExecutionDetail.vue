<template>
  <div v-loading="loading" class="execution-detail">
    <!-- 操作栏 -->
    <div class="action-bar">
      <div class="action-left">
        <el-button
          v-if="detail.failed_cases > 0"
          type="warning"
          :loading="rerunning"
          @click="handleRerunFailed"
        >
          重新执行失败用例
        </el-button>
        <el-button
          v-if="isCompleted"
          type="primary"
          @click="handleViewReport"
        >
          查看报告
        </el-button>
        <el-button
          v-if="isRunning"
          type="danger"
          :loading="stopping"
          @click="handleStop"
        >
          停止
        </el-button>
      </div>
      <div class="action-right">
        <el-button @click="handleExportJSON">导出 JSON</el-button>
        <el-button @click="handleExportCSV">导出 CSV</el-button>
      </div>
    </div>

    <!-- 概览卡片 -->
    <div class="overview-section">
      <div class="overview-header">
        <div class="overview-title">
          <h2>{{ detail.batch_name || '执行详情' }}</h2>
          <div class="overview-tags">
            <ExecutionStatusTag :status="detail.status || 'running'" />
            <el-tag v-if="detail.execution_mode" size="small" effect="plain">
              {{ detail.execution_mode === 'headed' ? '前台执行' : '后台执行' }}
            </el-tag>
            <span v-if="detail.batch_name" class="batch-name">{{ detail.batch_name }}</span>
          </div>
        </div>
      </div>

      <div class="stat-cards">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">总用例数</div>
          <div class="stat-value">{{ detail.total_cases ?? 0 }}</div>
        </el-card>
        <el-card shadow="hover" class="stat-card stat-passed">
          <div class="stat-label">通过</div>
          <div class="stat-value">{{ detail.passed_cases ?? 0 }}</div>
        </el-card>
        <el-card shadow="hover" class="stat-card stat-failed">
          <div class="stat-label">失败</div>
          <div class="stat-value">{{ detail.failed_cases ?? 0 }}</div>
        </el-card>
        <el-card shadow="hover" class="stat-card stat-skipped">
          <div class="stat-label">跳过</div>
          <div class="stat-value">{{ detail.skipped ?? 0 }}</div>
        </el-card>
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">通过率</div>
          <div class="stat-value">{{ passRateText }}</div>
        </el-card>
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">总耗时</div>
          <div class="stat-value">{{ totalDuration }}</div>
        </el-card>
      </div>

      <el-progress
        :percentage="passRate"
        :color="passRateColor"
        :stroke-width="14"
        style="margin-top: 16px"
      />
    </div>

    <!-- 用例结果表格 -->
    <el-card class="results-section">
      <template #header>
        <div class="results-header">
          <span>用例结果</span>
          <el-radio-group v-model="statusFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="success">成功</el-radio-button>
            <el-radio-button value="failed">失败</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <template v-if="filteredCaseResults.length">
        <el-table
          :data="filteredCaseResults"
          :row-class-name="tableRowClass"
          style="width: 100%"
          @row-click="onRowClick"
        >
          <el-table-column prop="case_name" label="用例名称" min-width="160" show-overflow-tooltip />
          <el-table-column label="优先级" width="80" align="center">
            <template #default="{ row }">
              <PriorityTag :priority="row.priority" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <ExecutionStatusTag :status="row.status" />
            </template>
          </el-table-column>
          <el-table-column label="步骤数" width="80" align="center">
            <template #default="{ row }">
              {{ row.steps?.length ?? row.step_count ?? '-' }}
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="100" align="center">
            <template #default="{ row }">
              {{ formatDuration(row.duration) }}
            </template>
          </el-table-column>
        </el-table>

        <!-- 步骤详情面板 -->
        <div v-if="expandedRow" class="step-detail-panel">
          <div class="step-header">
            <h4>{{ expandedRow.case_name }} - 步骤详情</h4>
            <el-button link type="primary" @click="expandedRow = null">收起</el-button>
          </div>

          <!-- 步骤时间线 -->
          <el-timeline class="step-timeline">
            <el-timeline-item
              v-for="step in expandedRow.steps"
              :key="step.step_index"
              :timestamp="step.duration_ms != null ? step.duration_ms + 'ms' : ''"
              :color="step.status === 'failed' ? '#f56c6c' : '#67c23a'"
              placement="top"
            >
              <div :class="['step-item', { 'step-failed': step.status === 'failed' }]">
                <div class="step-top">
                  <el-tag size="small" :type="step.status === 'failed' ? 'danger' : 'success'">
                    步骤 {{ step.step_index }}
                  </el-tag>
                  <el-tag size="small" type="warning">{{ step.action }}</el-tag>
                  <span v-if="step.target" class="step-target">{{ step.target }}</span>
                  <el-icon v-if="step.status === 'failed'" color="#f56c6c" :size="18">
                    <CircleClose />
                  </el-icon>
                  <el-icon v-else color="#67c23a" :size="18">
                    <CircleCheck />
                  </el-icon>
                </div>

                <!-- 截图对比 -->
                <div v-if="step.screenshot_before || step.screenshot_after" class="step-screenshots">
                  <div v-if="step.screenshot_before" class="screenshot-box">
                    <span class="screenshot-label">执行前</span>
                    <el-image
                      :src="getScreenshotUrl(step.screenshot_before)"
                      fit="contain"
                      class="screenshot-img"
                      :preview-src-list="[getScreenshotUrl(step.screenshot_before)]"
                      preview-teleported
                    />
                  </div>
                  <div v-if="step.screenshot_after" class="screenshot-box">
                    <span class="screenshot-label">执行后</span>
                    <el-image
                      :src="getScreenshotUrl(step.screenshot_after)"
                      fit="contain"
                      class="screenshot-img"
                      :preview-src-list="[getScreenshotUrl(step.screenshot_after)]"
                      preview-teleported
                    />
                  </div>
                </div>

                <!-- 日志输出 -->
                <el-collapse v-if="step.log_output" class="step-collapse">
                  <el-collapse-item title="日志输出">
                    <pre class="log-content">{{ step.log_output }}</pre>
                  </el-collapse-item>
                </el-collapse>

                <!-- 错误信息 -->
                <div v-if="step.status === 'failed' && step.error_message" class="step-error">
                  <div class="error-title">
                    <el-icon color="#f56c6c"><WarningFilled /></el-icon>
                    <span>错误信息</span>
                    <el-tag v-if="step.exception_type" size="small" type="danger" style="margin-left:8px">{{ step.exception_type }}</el-tag>
                  </div>
                  <pre class="error-body">{{ step.error_message }}</pre>
                  <div style="margin-top:8px">
                    <el-button
                      size="small"
                      type="warning"
                      :loading="healing"
                      @click="handleTriggerHeal(step)"
                    >
                      手动自愈
                    </el-button>
                  </div>
                </div>

                <!-- 自愈记录 -->
                <div v-if="step.heal_record" class="step-heal">
                  <el-tag type="warning" size="small" effect="dark">AI 已修复</el-tag>
                  <CodePreview
                    v-if="step.heal_record.code_diff"
                    :code="step.heal_record.code_diff"
                    :max-lines="10"
                    style="margin-top: 8px"
                  />
                </div>
              </div>
            </el-timeline-item>
          </el-timeline>
        </div>
      </template>

      <EmptyState v-else icon="Document" description="暂无用例结果" />
    </el-card>

    <!-- Headed 模式实时截图 -->
    <el-card
      v-if="detail.execution_mode === 'headed' && isRunning && latestScreenshot"
      class="headed-preview"
    >
      <template #header><span>实时截图</span></template>
      <el-image
        :src="getScreenshotUrl(latestScreenshot)"
        fit="contain"
        class="live-screenshot"
        :preview-src-list="[getScreenshotUrl(latestScreenshot)]"
        preview-teleported
      />
    </el-card>

    <!-- 自愈历史 -->
    <el-card class="heal-section" style="margin-top:16px">
      <template #header><span>自愈历史</span></template>
      <el-table v-if="healRecords.length > 0" :data="healRecords" size="small" stripe>
        <el-table-column label="用例" min-width="120">
          <template #default="{ row }">{{ row.case_name || row.case_id }}</template>
        </el-table-column>
        <el-table-column label="步骤" width="60" align="center">
          <template #default="{ row }">{{ row.step_index }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.retry_status === 'success' ? 'success' : 'danger'" size="small">
              {{ row.retry_status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="尝试次数" width="80" align="center">
          <template #default="{ row }">{{ row.retry_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState v-else icon="Document" description="暂无自愈记录" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { executionApi } from '@/api/execution'
import { reportApi } from '@/api/report'
import { healApi } from '@/api/heal'
import { useExecutionStore } from '@/stores/executionStore'
import { usePolling } from '@/composables/usePolling'
import ExecutionStatusTag from '@/components/ExecutionStatusTag.vue'
import PriorityTag from '@/components/PriorityTag.vue'
import CodePreview from '@/components/CodePreview.vue'
import EmptyState from '@/components/EmptyState.vue'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const executionStore = useExecutionStore()

const executionId = computed(() => route.params.executionId)

const detail = ref({})
const loading = ref(false)
const rerunning = ref(false)
const stopping = ref(false)
const healing = ref(false)
const healRecords = ref([])
const statusFilter = ref('all')
const expandedRow = ref(null)
const latestScreenshot = ref(null)

const FINAL_STATUSES = ['completed', 'stopped', 'failed', 'interrupted']

const isCompleted = computed(() => detail.value.status === 'completed')
const isRunning = computed(() => detail.value.status === 'running' || detail.value.status === 'healing')

const passRate = computed(() => {
  const total = detail.value.total_cases || 0
  if (total === 0) return 0
  return Math.round((detail.value.passed_cases || 0) / total * 100)
})

const passRateText = computed(() => {
  const total = detail.value.total_cases || 0
  if (total === 0) return '0.0%'
  return ((detail.value.passed_cases || 0) / total * 100).toFixed(1) + '%'
})

const passRateColor = computed(() => {
  if (passRate.value >= 90) return '#67c23a'
  if (passRate.value >= 60) return '#e6a23c'
  return '#f56c6c'
})

const totalDuration = computed(() => {
  return formatDuration(detail.value.total_duration ?? detail.value.duration)
})

const caseResults = computed(() => {
  const results = detail.value.case_results || []
  return results.map(r => ({
    ...r,
    steps: r.steps || [],
  }))
})

const filteredCaseResults = computed(() => {
  if (statusFilter.value === 'all') return caseResults.value
  return caseResults.value.filter(r => r.status === statusFilter.value)
})

function tableRowClass({ row }) {
  if (row.status === 'failed') return 'row-failed'
  return ''
}

function onRowClick(row) {
  if (expandedRow.value === row) {
    expandedRow.value = null
  } else {
    expandedRow.value = row
  }
}

function formatDuration(val) {
  if (val == null) return '-'
  const ms = Number(val)
  if (ms < 1000) return ms + 'ms'
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's'
  const minutes = Math.floor(ms / 60000)
  const seconds = ((ms % 60000) / 1000).toFixed(0)
  return minutes + 'm ' + seconds + 's'
}

function formatDateTime(val) {
  if (!val) return '-'
  if (typeof val === 'string') return val.replace('T', ' ').substring(0, 19)
  return new Date(val).toLocaleString('zh-CN')
}

function getScreenshotUrl(path) {
  if (!path) return ''
  // 统一路径分隔符为 /，去掉开头的 ./
  return '/' + path.replace(/\\/g, '/').replace(/^\.?\/?/, '')
}

async function fetchDetail() {
  const res = await executionApi.detail(executionId.value)
  detail.value = res.data || {}
  executionStore.currentExecution = detail.value
}

async function pollStatus() {
  const res = await executionApi.status(executionId.value)
  const data = res.data || {}
  detail.value.status = data.status
  detail.value.passed_cases = data.passed_cases ?? detail.value.passed_cases
  detail.value.failed_cases = data.failed_cases ?? detail.value.failed_cases
  detail.value.skipped = data.skipped ?? detail.value.skipped
  detail.value.total_cases = data.total_cases ?? detail.value.total_cases
  detail.value.total_duration = data.total_duration ?? data.duration ?? detail.value.total_duration
  if (data.case_results) {
    detail.value.case_results = data.case_results
  }
  if (data.latest_screenshot) {
    latestScreenshot.value = data.latest_screenshot
  }
  if (data.status && FINAL_STATUSES.includes(data.status)) {
    polling.stop()
    await fetchDetail()
  }
}

const polling = usePolling(pollStatus, 3000)

onMounted(async () => {
  loading.value = true
  try {
    await fetchDetail()
    await fetchHealRecords()
    if (detail.value.status && !FINAL_STATUSES.includes(detail.value.status)) {
      polling.start()
    }
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  polling.stop()
})

async function handleRerunFailed() {
  rerunning.value = true
  try {
    const failedCases = caseResults.value
      .filter(r => r.status === 'failed')
      .map(r => r.case_id || r.id)
    if (!failedCases.length) {
      ElMessage.warning('没有失败的用例')
      return
    }
    await executionApi.create(detail.value.project_id, {
      case_ids: failedCases,
      mode: detail.value.execution_mode || 'headless',
      batch_name: (detail.value.batch_name || '') + ' (重试)',
    })
    ElMessage.success('已创建重新执行任务')
    router.push('/projects/' + (detail.value.project_id || '') + '/executions')
  } catch {
    ElMessage.error('创建重新执行失败')
  } finally {
    rerunning.value = false
  }
}

async function handleViewReport() {
  try {
    // 先尝试获取已有报告
    let res = await reportApi.getInfo(executionId.value)
    let reportUrl = res.data?.download_url || res.data?.url || res.data?.report_url

    // 如果报告不存在，自动生成
    if (!reportUrl) {
      const genRes = await reportApi.generate(executionId.value)
      reportUrl = genRes.data?.download_url || genRes.data?.url
    }

    if (reportUrl) {
      window.open(reportUrl, '_blank')
    } else {
      ElMessage.info('报告生成失败，请稍后重试')
    }
  } catch {
    ElMessage.error('获取报告信息失败')
  }
}

async function handleStop() {
  stopping.value = true
  try {
    await executionApi.stop(executionId.value)
    ElMessage.success('已发送停止指令')
    polling.stop()
    await fetchDetail()
  } catch {
    ElMessage.error('停止执行失败')
  } finally {
    stopping.value = false
  }
}

async function handleTriggerHeal(step) {
  healing.value = true
  try {
    await healApi.triggerHeal(executionId.value, step.case_id, step.step_index)
    ElMessage.success('自愈触发成功')
    await fetchHealRecords()
    await fetchDetail()
  } catch {
    ElMessage.error('自愈触发失败')
  } finally {
    healing.value = false
  }
}

async function fetchHealRecords() {
  try {
    const res = await healApi.getHealRecords(executionId.value)
    healRecords.value = res.data?.items || res.data || []
  } catch {
    // 静默失败
  }
}

function handleExportJSON() {
  const data = {
    batch_name: detail.value.batch_name,
    status: detail.value.status,
    total_cases: detail.value.total_cases,
    passed: detail.value.passed_cases,
    failed: detail.value.failed_cases,
    skipped: detail.value.skipped,
    total_duration: detail.value.total_duration,
    case_results: detail.value.case_results || [],
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  downloadBlob(blob, 'execution-' + executionId.value + '.json')
  ElMessage.success('导出 JSON 成功')
}

function handleExportCSV() {
  const results = caseResults.value
  if (!results.length) {
    ElMessage.warning('没有可导出的数据')
    return
  }
  const headers = ['case_name', 'priority', 'status', 'step_count', 'duration']
  const rows = results.map(r => [
    r.case_name || '',
    r.priority || '',
    r.status || '',
    r.steps?.length ?? r.step_count ?? 0,
    r.duration ?? '',
  ])
  const csv = [headers.join(','), ...rows.map(row => row.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(','))].join('\n')
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
  downloadBlob(blob, 'execution-' + executionId.value + '.csv')
  ElMessage.success('导出 CSV 成功')
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.execution-detail {
  max-width: 1100px;
  margin: 0 auto;
  padding-bottom: 40px;
}

/* 操作栏 */
.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.action-left,
.action-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 概览 */
.overview-section {
  margin-bottom: 20px;
}

.overview-header {
  margin-bottom: 16px;
}

.overview-title h2 {
  margin: 0 0 8px;
  font-size: 1.25rem;
}

.overview-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.batch-name {
  font-size: 0.85rem;
  color: var(--text-secondary);
}

/* 统计卡片 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}

.stat-card {
  text-align: center;
}

.stat-label {
  font-size: 0.82rem;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.stat-value {
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-passed .stat-value {
  color: #67c23a;
}

.stat-failed .stat-value {
  color: #f56c6c;
}

.stat-skipped .stat-value {
  color: #909399;
}

/* 结果表格 */
.results-section {
  margin-bottom: 20px;
}

.results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.results-section :deep(.row-failed) {
  background-color: #fef0f0 !important;
}

.results-section :deep(.row-failed:hover > td) {
  background-color: #fde2e2 !important;
}

.results-section :deep(.el-table__row) {
  cursor: pointer;
}

/* 步骤详情面板 */
.step-detail-panel {
  margin-top: 16px;
  padding: 16px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 8px;
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.step-header h4 {
  margin: 0;
  font-size: 1rem;
}

.step-timeline {
  margin-left: 8px;
}

.step-item {
  padding: 4px 0;
}

.step-failed {
  background: #fef0f0;
  border-radius: 6px;
  padding: 8px 12px;
  border: 1px solid #fde2e2;
}

.step-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.step-target {
  font-size: 0.85rem;
  color: var(--text-regular);
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 截图 */
.step-screenshots {
  display: flex;
  gap: 16px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.screenshot-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
}

.screenshot-label {
  font-size: 0.78rem;
  color: var(--text-secondary);
}

.screenshot-img {
  width: 240px;
  max-height: 200px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  cursor: pointer;
}

.screenshot-img :deep(img) {
  max-height: 200px;
  object-fit: contain;
}

/* 日志 */
.step-collapse {
  margin-top: 8px;
}

.log-content {
  margin: 0;
  padding: 8px 12px;
  background: #1e1e2e;
  color: #cdd6f4;
  border-radius: 4px;
  font-size: 0.78rem;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow: auto;
  font-family: 'SF Mono', Consolas, monospace;
}

/* 错误 */
.step-error {
  margin-top: 8px;
  padding: 10px 12px;
  background: #fff0f0;
  border-radius: 6px;
  border: 1px solid #fde2e2;
}

.error-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  font-weight: 600;
  color: #f56c6c;
  margin-bottom: 6px;
}

.error-body {
  margin: 0;
  color: #f56c6c;
  font-size: 0.82rem;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: 'SF Mono', Consolas, monospace;
}

/* 自愈 */
.step-heal {
  margin-top: 10px;
  padding: 8px 12px;
  background: #fdf6ec;
  border-radius: 6px;
  border: 1px solid #faecd8;
}

/* Headed 实时截图 */
.headed-preview {
  margin-top: 20px;
}

.live-screenshot {
  width: 100%;
  max-height: 480px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
}

.live-screenshot :deep(img) {
  max-height: 480px;
  object-fit: contain;
}
</style>
