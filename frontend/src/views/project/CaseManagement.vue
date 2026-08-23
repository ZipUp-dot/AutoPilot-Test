<template>
  <div class="case-management">
    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-button type="primary" @click="openImportDialog">导入Excel</el-button>
        <el-button @click="downloadTemplate">
          <el-icon style="margin-right:4px"><Download /></el-icon>
          下载模板
        </el-button>
        <el-button
          type="danger"
          :disabled="selectedCases.length === 0"
          @click="handleBatchDelete"
        >
          批量删除
        </el-button>
        <el-button
          type="success"
          :disabled="selectedCases.length === 0"
          @click="handleBatchGenerate"
        >
          批量生成
        </el-button>
      </div>
    </div>

    <!-- 批量生成进度条 -->
    <div v-if="caseStore.generateProgress.status === 'running'" class="generate-progress-bar">
      <el-alert type="info" :closable="false" show-icon>
        <template #title>
          <span>批量代码生成中…</span>
          <el-progress
            :percentage="caseStore.generateProgress.progressPct || 0"
            :format="() => `${caseStore.generateProgress.completed + caseStore.generateProgress.failed}/${caseStore.generateProgress.total}`"
            style="width: 300px; margin-left: 16px"
          />
        </template>
      </el-alert>
    </div>

    <!-- 搜索与筛选 -->
    <div class="filter-bar">
      <el-input
        v-model="searchKeyword"
        placeholder="搜索用例名称或编号…"
        clearable
        style="width: 260px"
        @input="onSearchInput"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-select
        v-model="priorityFilter"
        placeholder="优先级"
        clearable
        style="width: 120px"
        @change="onFilterChange"
      >
        <el-option label="P0" value="P0" />
        <el-option label="P1" value="P1" />
        <el-option label="P2" value="P2" />
        <el-option label="P3" value="P3" />
      </el-select>
      <el-select
        v-model="statusFilter"
        placeholder="状态"
        clearable
        style="width: 140px"
        @change="onFilterChange"
      >
        <el-option label="待处理" value="pending" />
        <el-option label="已导入" value="imported" />
        <el-option label="已生成" value="generated" />
      </el-select>
    </div>

    <!-- 用例表格 -->
    <el-table
      v-if="cases.length > 0"
      v-loading="loading"
      :data="cases"
      style="width: 100%"
      row-key="id"
      @selection-change="handleSelectionChange"
      @row-click="openDetailDialog"
    >
      <el-table-column type="selection" width="50" reserve-selection />
      <el-table-column prop="case_no" label="编号" width="120" show-overflow-tooltip />
      <el-table-column prop="case_name" label="用例名称" min-width="200" show-overflow-tooltip />
      <el-table-column label="优先级" width="90">
        <template #default="{ row }">
          <PriorityTag :priority="row.priority" />
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <CaseStatusTag :status="row.status" />
        </template>
      </el-table-column>
      <el-table-column label="步骤数" width="80" align="center">
        <template #default="{ row }">
          {{ (row.steps || []).length }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="handleGenerate(row)">
            生成代码
          </el-button>
          <el-button link type="danger" size="small" @click.stop="handleDelete(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态 -->
    <EmptyState
      v-else-if="!loading"
      icon="Document"
      description="暂无测试用例，请导入Excel文件"
    >
      <el-button type="primary" @click="openImportDialog">导入用例</el-button>
    </EmptyState>

    <!-- 分页 -->
    <el-pagination
      v-if="total > 0"
      v-model:current-page="pagination.page"
      v-model:page-size="pagination.size"
      :total="total"
      :page-sizes="[10, 20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      style="margin-top: 16px; justify-content: flex-end"
      @current-change="fetchCases"
      @size-change="onSizeChange"
    />

    <!-- 导入对话框 -->
    <el-dialog v-model="importDialogVisible" title="导入用例" width="520px" destroy-on-close>
      <div class="import-body">
        <el-upload
          v-if="!importing"
          drag
          :auto-upload="false"
          :limit="1"
          accept=".xlsx,.xls"
          :on-change="handleFileChange"
          :file-list="fileList"
        >
          <el-icon :size="40" color="#409eff"><Upload /></el-icon>
          <div class="el-upload__text">拖拽文件到此处 或 <em>点击上传</em></div>
          <template #tip>
            <div class="el-upload__tip">仅支持 .xlsx / .xls 格式</div>
          </template>
        </el-upload>

        <div v-else class="import-progress">
          <el-progress
            :percentage="importProgress"
            :status="importProgress === 100 ? (importResult?.fail ? 'warning' : 'success') : ''"
          />
          <p v-if="importResult" class="import-result">
            导入完成：成功 {{ importResult.success }} 条，失败 {{ importResult.fail }} 条
          </p>
          <div v-if="importErrors.length > 0" class="import-errors">
            <p class="error-title">错误详情：</p>
            <ul>
              <li v-for="(err, idx) in importErrors" :key="idx">{{ err }}</li>
            </ul>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!uploadFile || importing"
          :loading="importing"
          @click="handleImport"
        >
          开始导入
        </el-button>
      </template>
    </el-dialog>

    <!-- 详情对话框 -->
    <el-dialog
      v-model="detailDialogVisible"
      :title="detailCase?.case_name || '用例详情'"
      width="720px"
      destroy-on-close
    >
      <div v-if="detailCase" class="detail-body">
        <div class="detail-meta">
          <span><strong>编号：</strong>{{ detailCase.case_no }}</span>
          <span><strong>优先级：</strong><PriorityTag :priority="detailCase.priority" /></span>
          <span><strong>状态：</strong><CaseStatusTag :status="detailCase.status" /></span>
        </div>

        <div class="detail-section">
          <h4>测试步骤</h4>
          <div v-if="detailSteps.length === 0" class="no-steps">暂无步骤</div>
          <div v-else class="steps-timeline">
            <div
              v-for="(step, idx) in detailSteps"
              :key="idx"
              class="timeline-item"
            >
              <div class="timeline-marker">{{ idx + 1 }}</div>
              <div class="timeline-content">
                <el-tag :type="actionTagType(step.action)" size="small" effect="plain">
                  {{ step.action }}
                </el-tag>
                <p class="step-desc">{{ step.description }}</p>
              </div>
            </div>
          </div>
        </div>

        <div v-if="detailCode" class="detail-section">
          <h4>生成代码</h4>
          <CodePreview :code="detailCode" />
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Upload, Download, Search } from '@element-plus/icons-vue'
import api from '@/api/index'
import { caseApi } from '@/api/case'
import { generateApi } from '@/api/generate'
import { useCaseStore } from '@/stores/caseStore'
import PriorityTag from '@/components/PriorityTag.vue'
import CaseStatusTag from '@/components/CaseStatusTag.vue'
import CodePreview from '@/components/CodePreview.vue'
import EmptyState from '@/components/EmptyState.vue'

const route = useRoute()
const projectId = computed(() => Number(route.params.id))

const caseStore = useCaseStore()

// 列表
const cases = ref([])
const loading = ref(false)
const total = ref(0)
const pagination = reactive({ page: 1, size: 20 })

// 搜索与筛选
const searchKeyword = ref('')
const priorityFilter = ref('')
const statusFilter = ref('')
let debounceTimer = null

function onSearchInput() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    pagination.page = 1
    fetchCases()
  }, 300)
}

function onFilterChange() {
  pagination.page = 1
  fetchCases()
}

function onSizeChange() {
  pagination.page = 1
  fetchCases()
}

// 多选
const selectedCases = ref([])

function handleSelectionChange(selection) {
  selectedCases.value = selection
}

// 获取用例列表
async function fetchCases() {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      size: pagination.size,
      keyword: searchKeyword.value || undefined,
      priority: priorityFilter.value || undefined,
      status: statusFilter.value || undefined,
    }
    Object.keys(params).forEach(k => params[k] === undefined && delete params[k])

    const res = await api.get(`/projects/${projectId.value}/cases/`, { params })
    const data = res.data
    cases.value = data?.items || []
    total.value = data?.total || 0
  } catch {
    ElMessage.error('获取用例列表失败')
  } finally {
    loading.value = false
  }
}

// 下载模板
function downloadTemplate() {
  const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
  window.open(`${baseURL}/projects/${projectId.value}/cases/template`, '_blank')
}

// ====== 导入 ======
const importDialogVisible = ref(false)
const importing = ref(false)
const importProgress = ref(0)
const importResult = ref(null)
const importErrors = ref([])
const uploadFile = ref(null)
const fileList = ref([])

function openImportDialog() {
  uploadFile.value = null
  fileList.value = []
  importing.value = false
  importProgress.value = 0
  importResult.value = null
  importErrors.value = []
  importDialogVisible.value = true
}

function handleFileChange(file) {
  uploadFile.value = file.raw
}

async function handleImport() {
  if (!uploadFile.value) {
    ElMessage.warning('请选择文件')
    return
  }
  importing.value = true
  importProgress.value = 30
  try {
    const formData = new FormData()
    formData.append('file', uploadFile.value)
    importProgress.value = 60
    const data = await caseStore.importExcel(projectId.value, formData)
    importProgress.value = 100
    importResult.value = {
      success: data?.success_count ?? data?.success ?? 0,
      fail: data?.failed ?? data?.fail_count ?? data?.fail ?? 0,
    }
    importErrors.value = data?.errors ?? data?.error_details ?? []
    if (importResult.value.success > 0 && importResult.value.fail === 0) {
      ElMessage.success(`导入完成，成功 ${importResult.value.success} 条`)
    } else if (importResult.value.success > 0) {
      ElMessage.warning(`导入完成，成功 ${importResult.value.success} 条，失败 ${importResult.value.fail} 条`)
    } else {
      ElMessage.error(`导入失败：成功 0 条，失败 ${importResult.value.fail} 条，请检查文件格式是否与模板一致`)
    }
    await fetchCases()
  } catch {
    importProgress.value = 0
    ElMessage.error('导入失败')
  } finally {
    importing.value = false
  }
}

// ====== 删除 ======
async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除用例「${row.case_name}」？`, '删除确认', {
      type: 'warning',
    })
    await caseStore.deleteCase(projectId.value, row.id)
    ElMessage.success('删除成功')
    await fetchCases()
  } catch {
    // 取消
  }
}

async function handleBatchDelete() {
  const names = selectedCases.value.map(c => c.case_name).join('、')
  try {
    await ElMessageBox.confirm(`确定删除以下用例？${names}`, '批量删除', { type: 'warning' })
    const ids = selectedCases.value.map(c => c.id)
    await caseStore.deleteBatch(projectId.value, ids)
    ElMessage.success('批量删除成功')
    selectedCases.value = []
    await fetchCases()
  } catch {
    // 取消
  }
}

// ====== 生成 ======
async function handleGenerate(row) {
  try {
    await caseStore.generateCode(projectId.value, row.id)
    ElMessage.success('代码生成成功')
    await fetchCases()
  } catch {
    ElMessage.error('代码生成失败')
  }
}

async function handleBatchGenerate() {
  try {
    const ids = selectedCases.value.map(c => c.id)
    await caseStore.generateBatch(projectId.value, ids)
    selectedCases.value = []
    ElMessage.success('批量生成已启动，后台执行中…')
    // 轮询完成时会自动刷新列表
  } catch {
    ElMessage.error('批量生成失败')
  }
}

// ====== 详情对话框 ======
const detailDialogVisible = ref(false)
const detailCase = ref(null)
const detailSteps = ref([])
const detailCode = ref('')

async function openDetailDialog(row) {
  detailCase.value = row
  detailSteps.value = row.steps || []
  detailCode.value = ''
  detailDialogVisible.value = true

  try {
    const res = await caseApi.detail(projectId.value, row.id)
    const data = res.data || res
    detailSteps.value = data.steps || row.steps || []
  } catch {
    // 使用已加载的 steps
  }

  if (row.status === 'generated') {
    try {
      const codeData = await caseStore.fetchCode(projectId.value, row.id)
      detailCode.value = codeData?.code ?? codeData?.content ?? ''
    } catch {
      // 无代码
    }
  }
}

const ACTION_TAG_MAP = {
  click: 'primary',
  input: 'success',
  navigate: '',
  wait: 'warning',
  assert: 'danger',
  scroll: 'info',
  select: 'success',
  hover: '',
}

function actionTagType(action) {
  return ACTION_TAG_MAP[action?.toLowerCase()] || 'info'
}

// 监听 projectId 变化（切换项目时重新加载）
watch(projectId, () => {
  pagination.page = 1
  fetchCases()
})

onMounted(() => {
  fetchCases()
})
</script>

<style scoped>
.case-management {
  padding: 4px 0;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.toolbar-left {
  display: flex;
  gap: 8px;
}

.generate-progress-bar {
  margin-bottom: 12px;
}

.filter-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

/* 导入 */
.import-body {
  min-height: 120px;
}

.import-progress {
  padding: 20px 0;
  text-align: center;
}

.import-result {
  margin-top: 12px;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.import-errors {
  margin-top: 12px;
  text-align: left;
  max-height: 180px;
  overflow-y: auto;
  background: #fef0f0;
  border-radius: 6px;
  padding: 10px 14px;
}

.error-title {
  font-weight: 600;
  color: #f56c6c;
  margin: 0 0 6px;
}

.import-errors ul {
  margin: 0;
  padding-left: 18px;
}

.import-errors li {
  font-size: 0.82rem;
  color: #e64242;
  line-height: 1.6;
}

/* 详情 */
.detail-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.detail-meta {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  align-items: center;
  font-size: 0.9rem;
}

.detail-section h4 {
  margin: 0 0 10px;
  font-size: 0.95rem;
  font-weight: 600;
}

.no-steps {
  color: var(--text-secondary);
  font-size: 0.85rem;
}

.steps-timeline {
  position: relative;
  padding-left: 8px;
}

.timeline-item {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  position: relative;
}

.timeline-item:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 17px;
  top: 30px;
  bottom: 0;
  width: 2px;
  background: #e4e7ed;
}

.timeline-marker {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
  z-index: 1;
}

.timeline-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.step-desc {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-regular);
  line-height: 1.5;
}
</style>
