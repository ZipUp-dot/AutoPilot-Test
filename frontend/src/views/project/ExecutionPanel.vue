<template>
  <div class="execution-panel">
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="openWizard">新建执行</el-button>
    </div>

    <!-- 实时进度区域 -->
    <div v-if="runningExecutionId && executionStatus" class="progress-card">
      <div class="progress-header">
        <h3>执行中</h3>
        <ExecutionStatusTag :status="executionStatus.status || 'running'" />
        <el-button
          v-if="isRunning"
          type="danger"
          size="small"
          :loading="stopping"
          @click="handleStopExecution"
          style="margin-left: auto"
        >
          停止
        </el-button>
      </div>

      <div class="progress-body">
        <div class="progress-stats">
          <span class="stat-passed">通过: {{ executionStatus.passed_cases ?? 0 }}</span>
          <span class="stat-failed">失败: {{ executionStatus.failed_cases ?? 0 }}</span>
          <span>总计: {{ executionStatus.total_cases ?? 0 }}</span>
        </div>
        <el-progress
          :percentage="progressPercent"
          :status="progressBarStatus"
          :stroke-width="16"
        />
        <p class="progress-hint">
          {{ executionStatus.current_case_name || executionStatus.message || '等待执行...' }}
        </p>
      </div>

      <div v-if="executionStatus.status === 'completed'" class="progress-actions">
        <el-button
          type="success"
          :loading="generatingReport"
          @click="handleGenerateReport"
        >
          生成报告
        </el-button>
        <el-button
          v-if="reportGenerated"
          type="primary"
          @click="handleViewReport"
        >
          查看报告
        </el-button>
      </div>
    </div>

    <!-- 执行历史表格 -->
    <el-table
      v-if="executionStore.executions.length > 0"
      :data="executionStore.executions"
      v-loading="historyLoading"
      style="width: 100%"
      @row-click="handleRowClick"
      highlight-current-row
    >
      <el-table-column prop="batch_name" label="批次名称" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="row-link">{{ row.batch_name || '-' }}</span>
          <el-tag v-if="row.platform === 'android'" type="success" size="small" style="margin-left:6px">Android</el-tag>
          <el-tag v-else-if="row.platform === 'web'" size="small" style="margin-left:6px">Web</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="执行模式" width="110" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row.execution_mode === 'headed' ? 'warning' : 'info'">
            {{ row.execution_mode === 'headed' ? '前台执行' : '后台执行' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="200">
        <template #default="{ row }">
          <div class="progress-cell">
            <el-progress
              :percentage="getRowProgress(row)"
              :stroke-width="8"
              :color="getRowProgressColor(row)"
            />
            <span class="progress-text">{{ row.passed_cases ?? 0 }}/{{ row.total_cases ?? 0 }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <ExecutionStatusTag :status="row.status" />
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="100" align="center">
        <template #default="{ row }">
          {{ formatDuration(row.duration) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" align="center" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="handleViewDetail(row)">
            详情
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态 -->
    <EmptyState
      v-else-if="!historyLoading"
      icon="VideoPlay"
      description="暂无执行记录"
    >
      <el-button type="primary" @click="openWizard">新建执行</el-button>
    </EmptyState>

    <!-- 新建执行向导对话框 -->
    <el-dialog
      v-model="wizardVisible"
      title="新建执行"
      width="640px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <el-steps :active="wizardStep" align-center style="margin-bottom: 28px">
        <el-step title="选择用例" />
        <el-step title="配置参数" />
        <el-step title="确认启动" />
      </el-steps>

      <!-- Step 1: 选择用例 -->
      <div v-show="wizardStep === 0">
        <div v-if="loadingCases" v-loading="loadingCases" style="height: 160px" />
        <EmptyState
          v-else-if="generatedCases.length === 0"
          icon="Document"
          description="暂无已生成代码的用例"
        />
        <el-table
          v-else
          ref="wizardTableRef"
          :data="generatedCases"
          size="small"
          max-height="360"
          @selection-change="onWizardSelectionChange"
        >
          <el-table-column type="selection" width="44" />
          <el-table-column prop="case_name" label="用例名称" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag type="success" size="small" effect="plain">已生成</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- Step 2: 配置参数 -->
      <div v-show="wizardStep === 1" class="step-config">
        <div class="config-item">
          <label class="config-label">执行模式</label>
          <el-radio-group v-model="wizardMode">
            <el-radio value="headless">后台执行 (Headless)</el-radio>
            <el-radio value="headed">前台执行 (Headed)</el-radio>
          </el-radio-group>
        </div>
        <div class="config-item">
          <label class="config-label">批次名称</label>
          <el-input
            v-model="wizardBatchName"
            placeholder="请输入批次名称（可选）"
            clearable
            maxlength="64"
          />
        </div>
      </div>

      <!-- Step 3: 确认启动 -->
      <div v-show="wizardStep === 2" class="step-summary">
        <div class="summary-card">
          <div class="summary-row">
            <span class="summary-label">选中用例数</span>
            <span class="summary-value">{{ wizardSelectedCases.length }}</span>
          </div>
          <div class="summary-row">
            <span class="summary-label">执行模式</span>
            <span class="summary-value">
              <el-tag size="small" :type="wizardMode === 'headed' ? 'warning' : 'info'">
                {{ wizardMode === 'headed' ? 'Headed' : 'Headless' }}
              </el-tag>
            </span>
          </div>
          <div class="summary-row">
            <span class="summary-label">批次名称</span>
            <span class="summary-value">{{ wizardBatchName || '（未设置）' }}</span>
          </div>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="wizardVisible = false">取消</el-button>
          <el-button v-if="wizardStep > 0" @click="wizardStep--">上一步</el-button>
          <el-button
            v-if="wizardStep < 2"
            type="primary"
            :disabled="!canGoNext"
            @click="wizardStep++"
          >
            下一步
          </el-button>
          <el-button
            v-if="wizardStep === 2"
            type="primary"
            :loading="creating"
            @click="handleStartExecution"
          >
            启动执行
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { executionApi } from '@/api/execution'
import { reportApi } from '@/api/report'
import { caseApi } from '@/api/case'
import { useExecutionStore } from '@/stores/executionStore'
import { useReportStore } from '@/stores/reportStore'
import ExecutionStatusTag from '@/components/ExecutionStatusTag.vue'
import EmptyState from '@/components/EmptyState.vue'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number(route.params.id))

const executionStore = useExecutionStore()
const reportStore = useReportStore()

// ==================== 执行历史 ====================
const historyLoading = ref(false)

async function loadExecutions() {
  historyLoading.value = true
  try {
    await executionStore.fetchExecutions(projectId.value)
    // 检查是否有正在运行的执行，自动恢复轮询
    const running = executionStore.executions.find(
      e => e.status === 'running' || e.status === 'healing'
    )
    if (running) {
      runningExecutionId.value = running.id
      executionStore.startPolling(running.id)
      reportGenerated.value = false
    }
  } catch {
    ElMessage.error('获取执行历史失败')
  } finally {
    historyLoading.value = false
  }
}

function getRowProgress(row) {
  const total = row.total_cases ?? 1
  const done = (row.passed_cases ?? 0) + (row.failed_cases ?? 0)
  return Math.round((done / total) * 100)
}

function getRowProgressColor(row) {
  if (row.status === 'completed' || row.status === 'success') return '#67c23a'
  if (row.status === 'failed') return '#f56c6c'
  return '#409eff'
}

function formatDuration(val) {
  if (val == null) return '-'
  let seconds = val
  if (seconds >= 3600) {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    return `${h}h ${m}m`
  }
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60)
    const s = Math.floor(seconds % 60)
    return `${m}m ${s}s`
  }
  return `${Math.floor(seconds)}s`
}

function handleRowClick(row) {
  router.push(`/executions/${row.id}`)
}

function handleViewDetail(row) {
  router.push(`/executions/${row.id}`)
}

// ==================== 新建执行向导 ====================
const wizardVisible = ref(false)
const wizardStep = ref(0)
const wizardTableRef = ref(null)

const loadingCases = ref(false)
const allCases = ref([])
const wizardSelectedCases = ref([])

const wizardMode = ref('headless')
const wizardBatchName = ref('')
const creating = ref(false)

const generatedCases = computed(() =>
  allCases.value.filter(c => c.status === 'generated')
)

const canGoNext = computed(() => {
  if (wizardStep.value === 0) return wizardSelectedCases.value.length > 0
  return true
})

function openWizard() {
  wizardStep.value = 0
  wizardSelectedCases.value = []
  wizardMode.value = 'headless'
  wizardBatchName.value = ''
  wizardVisible.value = true
  fetchCasesForWizard()
}

async function fetchCasesForWizard() {
  loadingCases.value = true
  try {
    const res = await caseApi.list(projectId.value, 1, 999)
    allCases.value = res.data?.items || []
  } catch {
    ElMessage.error('获取用例列表失败')
  } finally {
    loadingCases.value = false
  }
}

function onWizardSelectionChange(rows) {
  wizardSelectedCases.value = rows
}

// ==================== 实时进度 ====================
const runningExecutionId = ref(null)
const stopping = ref(false)
const generatingReport = ref(false)
const reportGenerated = ref(false)

const executionStatus = computed(() => executionStore.executionStatus)

const isRunning = computed(() => {
  const s = executionStatus.value?.status
  return s === 'running' || s === 'healing'
})

const progressPercent = computed(() => {
  const total = executionStatus.value?.total_cases || 1
  const done = (executionStatus.value?.passed_cases || 0) + (executionStatus.value?.failed_cases || 0)
  return Math.round((done / total) * 100)
})

const progressBarStatus = computed(() => {
  if (!executionStatus.value) return undefined
  if (executionStatus.value.status === 'completed') return 'success'
  if (executionStatus.value.status === 'failed') return 'exception'
  return undefined
})

// 监听执行状态变化 → 完成后自动生成报告
watch(
  () => executionStatus.value?.status,
  (newStatus) => {
    if (newStatus === 'completed' && runningExecutionId.value && !reportGenerated.value) {
      handleAutoGenerateReport()
    }
  }
)

async function handleAutoGenerateReport() {
  generatingReport.value = true
  try {
    await reportStore.generateReport(runningExecutionId.value)
    reportGenerated.value = true
  } catch {
    // silently fail
  } finally {
    generatingReport.value = false
  }
}

async function handleGenerateReport() {
  generatingReport.value = true
  try {
    await reportStore.generateReport(runningExecutionId.value)
    reportGenerated.value = true
    ElMessage.success('报告生成成功')
  } catch {
    ElMessage.error('报告生成失败')
  } finally {
    generatingReport.value = false
  }
}

function handleViewReport() {
  router.push(`/projects/${projectId.value}/reports`)
}

async function handleStopExecution() {
  stopping.value = true
  try {
    await executionStore.stopExecution(runningExecutionId.value)
    ElMessage.success('已停止执行')
    executionStore.stopPolling()
  } catch {
    ElMessage.error('停止执行失败')
  } finally {
    stopping.value = false
  }
}

// ==================== 启动执行 ====================
async function handleStartExecution() {
  creating.value = true
  try {
    const res = await executionStore.createExecution(projectId.value, {
      case_ids: wizardSelectedCases.value.map(c => c.id),
      mode: wizardMode.value,
      batch_name: wizardBatchName.value || undefined,
    })
    const execId = res?.id || res?.execution_id
    runningExecutionId.value = execId
    reportGenerated.value = false
    wizardVisible.value = false
    executionStore.startPolling(execId, 2000)
    ElMessage.success('执行已启动')
    await loadExecutions()
  } catch {
    ElMessage.error('创建执行失败')
  } finally {
    creating.value = false
  }
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadExecutions()
})

onUnmounted(() => {
  executionStore.stopPolling()
})
</script>

<style scoped>
.execution-panel {
  padding: 4px 0;
}

/* 工具栏 */
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

/* 实时进度卡片 */
.progress-card {
  background: var(--card-bg, #fff);
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
}

.progress-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.progress-header h3 {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary, #303133);
}

.progress-body {
  margin-bottom: 16px;
}

.progress-stats {
  display: flex;
  gap: 24px;
  margin-bottom: 12px;
  font-size: 0.9rem;
  color: var(--text-regular, #606266);
}

.stat-passed {
  color: var(--success, #67c23a);
  font-weight: 600;
}

.stat-failed {
  color: var(--danger, #f56c6c);
  font-weight: 600;
}

.progress-hint {
  margin-top: 8px;
  font-size: 0.82rem;
  color: var(--text-secondary, #909399);
}

.progress-actions {
  display: flex;
  gap: 12px;
}

/* 进度单元格 */
.progress-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.progress-text {
  font-size: 0.8rem;
  color: var(--text-secondary, #909399);
  white-space: nowrap;
}

/* 行链接样式 */
.row-link {
  color: var(--brand, #409eff);
  cursor: pointer;
}

/* 向导 - 第二步配置 */
.step-config {
  min-height: 180px;
}

.config-item {
  margin-bottom: 24px;
}

.config-label {
  display: block;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary, #303133);
  margin-bottom: 10px;
}

/* 向导 - 第三步确认 */
.step-summary {
  min-height: 160px;
}

.summary-card {
  background: var(--bg-secondary, #f5f7fa);
  border-radius: 8px;
  padding: 20px 24px;
}

.summary-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-color, #e4e7ed);
}

.summary-row:last-child {
  border-bottom: none;
}

.summary-label {
  font-size: 0.88rem;
  color: var(--text-secondary, #909399);
}

.summary-value {
  font-size: 0.9rem;
  color: var(--text-primary, #303133);
  font-weight: 500;
}

/* 对话框底部 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
