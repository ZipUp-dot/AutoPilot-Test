<template>
  <div class="ai-review">
    <div class="toolbar">
      <el-select v-model="filterStatus" placeholder="全部状态" clearable style="width: 160px" @change="reload">
        <el-option label="待评审" value="pending" />
        <el-option label="需修改" value="needs_edit" />
        <el-option label="已批准" value="approved" />
        <el-option label="已驳回" value="rejected" />
      </el-select>
      <el-button @click="reload">
        <el-icon style="margin-right:4px"><Refresh /></el-icon>
        刷新
      </el-button>
      <span class="hint">AI 候选草稿：评审通过并 Promote 后进入用例库</span>
    </div>

    <div class="layout">
      <!-- 左：候选列表（三维独立展示，8.5 永不合并） -->
      <el-table
        :data="store.drafts"
        v-loading="store.loading"
        highlight-current-row
        @current-change="onSelect"
        style="flex: 1"
      >
        <el-table-column prop="case_name" label="用例名称" min-width="180" />
        <el-table-column label="草稿" width="110">
          <template #default="{ row }">
            <span class="mono">v{{ row.draft_version }}</span>
          </template>
        </el-table-column>
        <el-table-column label="AI 自评" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="assessmentType(row.ai_assessment)">
              {{ row.ai_assessment || '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="系统校验" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.validation_status === 'valid' ? 'success' : 'danger'">
              {{ row.validation_status || '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="人工评审" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="reviewType(row.review_status)">
              {{ reviewLabel(row.review_status) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <!-- 右：详情编辑器 -->
      <div class="detail">
        <template v-if="store.current">
          <div class="detail-head">
            <el-input v-model="form.case_name" placeholder="用例名称" />
            <el-select v-model="form.priority" style="width: 100px">
              <el-option v-for="p in ['P0', 'P1', 'P2', 'P3']" :key="p" :label="p" :value="p" />
            </el-select>
          </div>

          <div class="dims">
            <el-tag size="small" effect="plain" :type="assessmentType(store.current.ai_assessment)">
              AI 自评：{{ store.current.ai_assessment || '—' }}
            </el-tag>
            <el-tag size="small" :type="store.current.validation_status === 'valid' ? 'success' : 'danger'">
              系统校验：{{ store.current.validation_status || '—' }}
            </el-tag>
            <el-tag size="small" :type="reviewType(store.current.review_status)">
              人工评审：{{ reviewLabel(store.current.review_status) }}
            </el-tag>
            <el-tag v-if="store.current.promoted_case_id" size="small" type="success" effect="dark">
              已 Promote（source=ai_draft）
            </el-tag>
          </div>

          <el-form label-width="80px" label-position="top">
            <el-form-item label="前置条件（每行一条）">
              <el-input v-model="preconditionsText" type="textarea" :rows="2" />
            </el-form-item>
            <el-form-item label="步骤">
              <DraftStepEditor v-model="form.steps" :evidence="evidence" />
            </el-form-item>
            <el-form-item label="预期结果">
              <el-input v-model="form.expected_result" type="textarea" :rows="2" />
            </el-form-item>
            <el-form-item label="评审意见">
              <el-input v-model="comment" type="textarea" :rows="2" placeholder="驳回 / 需修改时必填" />
            </el-form-item>
          </el-form>

          <div class="actions">
            <el-button :loading="saving" @click="saveVersion">保存为新版本</el-button>
            <el-button type="success" :loading="busy" @click="approve">批准</el-button>
            <el-button type="warning" :loading="busy" @click="needEdit">需修改</el-button>
            <el-button type="danger" :loading="busy" @click="reject">驳回</el-button>
            <el-button
              type="primary"
              :loading="busy"
              :disabled="!canPromote"
              @click="promote"
            >
              Promote 到用例库
            </el-button>
          </div>
        </template>
        <el-empty v-else description="选择左侧草稿查看详情" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAiDraftsStore } from '@/stores/aiDrafts'
import { elementApi } from '@/api/element'
import DraftStepEditor from '@/components/DraftStepEditor.vue'

const route = useRoute()
const router = useRouter()
const store = useAiDraftsStore()

const pid = computed(() => Number(route.params.id))
const filterStatus = ref(null)
const comment = ref('')
const saving = ref(false)
const busy = ref(false)
const evidence = ref([])
const form = ref({ case_name: '', priority: 'P1', steps: [], expected_result: '' })
const preconditionsText = ref('')

const canPromote = computed(() =>
  store.current?.validation_status === 'valid' && store.current?.review_status === 'approved'
)

function assessmentType(v) {
  if (v === 'recommended') return 'success'
  if (v === 'needs_review') return 'warning'
  return 'info'
}
function reviewType(v) {
  if (v === 'approved') return 'success'
  if (v === 'rejected') return 'danger'
  if (v === 'needs_edit') return 'warning'
  return 'info'
}
function reviewLabel(v) {
  return { pending: '待评审', needs_edit: '需修改', approved: '已批准', rejected: '已驳回' }[v] || v || '—'
}

async function reload() {
  await store.fetchDrafts(pid.value, filterStatus.value || null)
}

async function loadEvidence() {
  try {
    const res = await elementApi.list(pid.value, { page: 1, size: 200 })
    evidence.value = (res?.data?.items || []).map(e => ({ selector: e.selector, name: e.name }))
  } catch {
    evidence.value = []
  }
}

function onSelect(row) {
  if (row) selectDraft(row.id)
}

async function selectDraft(id) {
  const detail = await store.fetchDetail(id)
  if (!detail) return
  comment.value = ''
  preconditionsText.value = parseList(detail.preconditions).join('\n')
  form.value = {
    case_name: detail.case_name || '',
    priority: detail.priority || 'P1',
    steps: parseList(detail.steps),
    expected_result: detail.expected_result || '',
  }
}

function parseList(raw) {
  if (Array.isArray(raw)) return raw
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function buildPayload() {
  return {
    schema_version: 1,
    case_name: form.value.case_name,
    priority: form.value.priority,
    preconditions: preconditionsText.value.split('\n').map(s => s.trim()).filter(Boolean),
    steps: form.value.steps,
    expected_result: form.value.expected_result,
    ai_assessment: store.current?.ai_assessment || null,
  }
}

async function saveVersion() {
  saving.value = true
  try {
    await store.saveVersion(store.current.id, buildPayload(), pid.value)
    ElMessage.success('已保存为新版本（旧版本保留，只读）')
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function approve() {
  try {
    await ElMessageBox.confirm('批准后即可 Promote 进入用例库，确认批准？', '二次确认', {
      type: 'warning', confirmButtonText: '确认批准', cancelButtonText: '取消',
    })
  } catch {
    return   // 用户取消
  }
  await doReview('approved', comment.value)
}

async function needEdit() { await doReview('needs_edit', comment.value) }
async function reject() { await doReview('rejected', comment.value) }

async function doReview(status, remark) {
  busy.value = true
  try {
    await store.submitReview(store.current.id, status, remark)
    ElMessage.success('评审已提交')
  } catch (e) {
    if (e?.message && /必须填写/.test(e.message)) ElMessage.warning(e.message)
  } finally {
    busy.value = false
  }
}

async function promote() {
  busy.value = true
  try {
    const res = await store.promote(store.current.id, pid.value)
    ElMessage.success(`已进入用例库（source=${res?.source || 'ai_draft'}）`)
    router.push(`/projects/${pid.value}/cases`)
  } catch {
    if (store.snapshotNotice) {
      ElMessage.warning('页面已变化，请复核')
      await reload()
    }
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadEvidence()])
})
</script>

<style scoped>
.ai-review { display: flex; flex-direction: column; gap: 12px; }
.toolbar { display: flex; align-items: center; gap: 8px; }
.hint { color: var(--text-secondary); font-size: .82rem; margin-left: auto; }
.layout { display: flex; gap: 16px; align-items: flex-start; }
.detail { flex: 1; min-width: 420px; display: flex; flex-direction: column; gap: 10px; }
.detail-head { display: flex; gap: 8px; }
.dims { display: flex; flex-wrap: wrap; gap: 6px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
.mono { font-family: ui-monospace, monospace; }
</style>
