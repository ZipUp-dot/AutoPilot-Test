<template>
  <div class="mock-manage">
    <div class="page-header">
      <div>
        <h2>Mock 服务</h2>
        <div class="sub-title">逻辑命名空间（无端口无进程）：执行期由 Playwright route 拦截 base_path 前缀的请求。</div>
      </div>
      <el-space>
        <el-select v-model="projectId" placeholder="选择项目" style="width: 220px" @change="reload">
          <el-option v-for="p in projectStore.projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-button :disabled="!projectId" @click="reload">
          <el-icon style="margin-right:4px"><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="primary" :disabled="!projectId" @click="openCreateServer">
          <el-icon style="margin-right:4px"><Plus /></el-icon>新建 Mock 服务
        </el-button>
      </el-space>
    </div>

    <el-table :data="store.servers" v-loading="store.loading" border>
      <el-table-column prop="name" label="名称" min-width="150" />
      <el-table-column label="base_path" min-width="140">
        <template #default="{ row }"><span class="mono">{{ row.base_path }}</span></template>
      </el-table-column>
      <el-table-column label="启用" width="110">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="(v) => toggleServer(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="openRules(row)">规则</el-button>
          <el-button size="small" @click="openEditServer(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="removeServer(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-alert
      type="info" :closable="false" show-icon style="margin-top:16px"
      title="Mock 仅注入 Web 执行链；未匹配路径放行真实请求（不阻断）。Android 链不受影响。"
    />

    <!-- server 编辑 -->
    <el-dialog v-model="serverDialog" :title="editingServer ? '编辑 Mock 服务' : '新建 Mock 服务'" width="520px">
      <el-form :model="serverForm" label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="serverForm.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="base_path">
          <el-input v-model="serverForm.base_path" placeholder="/mock" maxlength="64" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="serverForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="serverDialog = false">取消</el-button>
        <el-button type="primary" @click="submitServer">保存</el-button>
      </template>
    </el-dialog>

    <!-- 规则编辑器 -->
    <el-drawer v-model="rulesDrawer" :title="`规则 — ${currentServer?.name || ''}`" size="62%">
      <el-space style="margin-bottom:12px">
        <el-button type="primary" size="small" @click="openCreateRule">
          <el-icon style="margin-right:4px"><Plus /></el-icon>新增规则
        </el-button>
        <el-button size="small" @click="refreshRules">刷新</el-button>
      </el-space>

      <el-table :data="store.rules" v-loading="rulesLoading" border size="small">
        <el-table-column prop="method" label="方法" width="90" />
        <el-table-column prop="path_pattern" label="路径模板" min-width="160">
          <template #default="{ row }"><span class="mono">{{ row.path_pattern }}</span></template>
        </el-table-column>
        <el-table-column prop="status_code" label="状态码" width="90" align="center" />
        <el-table-column prop="delay_ms" label="延迟(ms)" width="90" align="center" />
        <el-table-column label="启用" width="90" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => toggleRule(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEditRule(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="removeRule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-divider content-position="left">dry-run 匹配测试（无副作用）</el-divider>
      <el-space>
        <el-select v-model="dryRunForm.method" style="width:110px">
          <el-option v-for="m in METHODS" :key="m" :label="m" :value="m" />
        </el-select>
        <el-input v-model="dryRunForm.path" placeholder="/mock/api/users/1" style="width:280px" />
        <el-button :disabled="!dryRunForm.path" @click="runDryRun">测试匹配</el-button>
      </el-space>
      <el-alert
        v-if="dryRunResult"
        :type="dryRunResult.matched ? 'success' : 'warning'"
        :closable="false" show-icon style="margin-top:12px"
        :title="dryRunResult.matched
          ? `命中规则 #${dryRunResult.rule_id}（${dryRunResult.status_code}，${dryRunResult.delay_ms}ms）`
          : `未命中：${dryRunResult.reason || '将放行真实请求'}`"
      />

      <!-- 规则表单 -->
      <el-dialog v-model="ruleDialog" :title="editingRule ? '编辑规则' : '新增规则'" width="560px" append-to-body>
        <el-form :model="ruleForm" label-width="110px">
          <el-form-item label="请求方法" required>
            <el-select v-model="ruleForm.method" style="width:140px">
              <el-option v-for="m in METHODS" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <el-form-item label="路径模板" required>
            <el-input v-model="ruleForm.path_pattern" placeholder="/api/users/:id" maxlength="255" />
          </el-form-item>
          <el-form-item label="状态码">
            <el-input-number v-model="ruleForm.status_code" :min="100" :max="599" />
          </el-form-item>
          <el-form-item label="响应体 (JSON)">
            <el-input v-model="ruleForm.bodyText" type="textarea" :rows="4" placeholder='{"ok": true}' />
          </el-form-item>
          <el-form-item label="响应头 (JSON)">
            <el-input v-model="ruleForm.headersText" type="textarea" :rows="2" placeholder='{"Content-Type": "application/json"}' />
          </el-form-item>
          <el-form-item label="延迟(ms)">
            <el-input-number v-model="ruleForm.delay_ms" :min="0" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="ruleForm.enabled" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="ruleDialog = false">取消</el-button>
          <el-button type="primary" @click="submitRule">保存</el-button>
        </template>
      </el-dialog>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/projectStore'
import { useMockStore } from '@/stores/mock'

const METHODS = ['GET', 'POST', 'PUT', 'DELETE']

const store = useMockStore()
const projectStore = useProjectStore()

const projectId = ref(null)
const rulesLoading = ref(false)

// ── server ──
const serverDialog = ref(false)
const editingServer = ref(null)
const serverForm = reactive({ name: '', base_path: '/mock', enabled: true })

// ── rules ──
const rulesDrawer = ref(false)
const currentServer = ref(null)
const ruleDialog = ref(false)
const editingRule = ref(null)
const ruleForm = reactive({
  method: 'GET', path_pattern: '', status_code: 200,
  bodyText: '', headersText: '', delay_ms: 0, enabled: true,
})

// ── dry-run ──
const dryRunForm = reactive({ method: 'GET', path: '' })
const dryRunResult = ref(null)

function reload() {
  if (projectId.value) store.fetchServers(projectId.value)
}

// ── server 操作 ──
function openCreateServer() {
  editingServer.value = null
  Object.assign(serverForm, { name: '', base_path: '/mock', enabled: true })
  serverDialog.value = true
}

function openEditServer(row) {
  editingServer.value = row
  Object.assign(serverForm, { name: row.name, base_path: row.base_path, enabled: row.enabled })
  serverDialog.value = true
}

async function submitServer() {
  if (!serverForm.name.trim()) return ElMessage.warning('请填写名称')
  const body = { name: serverForm.name, base_path: serverForm.base_path || '/mock', enabled: serverForm.enabled }
  try {
    if (editingServer.value) await store.updateServer(editingServer.value.id, body, projectId.value)
    else await store.createServer(projectId.value, body)
    serverDialog.value = false
    ElMessage.success('已保存')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  }
}

async function toggleServer(row, v) {
  await store.updateServer(row.id, { enabled: v }, projectId.value)
}

async function removeServer(row) {
  await ElMessageBox.confirm(`确认删除 Mock 服务「${row.name}」？`, '删除', {
    confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
  })
  try {
    await store.removeServer(row.id, projectId.value)
    ElMessage.success('已删除')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '删除失败')
  }
}

// ── 规则操作 ──
function openRules(row) {
  currentServer.value = row
  dryRunResult.value = null
  Object.assign(dryRunForm, { method: 'GET', path: '' })
  rulesDrawer.value = true
  refreshRules()
}

async function refreshRules() {
  if (!currentServer.value) return
  rulesLoading.value = true
  try {
    await store.fetchRules(currentServer.value.id)
  } finally {
    rulesLoading.value = false
  }
}

function openCreateRule() {
  editingRule.value = null
  Object.assign(ruleForm, {
    method: 'GET', path_pattern: '', status_code: 200,
    bodyText: '', headersText: '', delay_ms: 0, enabled: true,
  })
  ruleDialog.value = true
}

function openEditRule(row) {
  editingRule.value = row
  Object.assign(ruleForm, {
    method: row.method,
    path_pattern: row.path_pattern,
    status_code: row.status_code,
    bodyText: row.response_body != null ? JSON.stringify(row.response_body, null, 2) : '',
    headersText: row.response_headers ? JSON.stringify(row.response_headers, null, 2) : '',
    delay_ms: row.delay_ms,
    enabled: row.enabled,
  })
  ruleDialog.value = true
}

function parseJson(text, label, fallback) {
  if (!text || !text.trim()) return fallback
  try {
    return JSON.parse(text)
  } catch (e) {
    ElMessage.error(`${label} JSON 解析失败`)
    throw e
  }
}

async function submitRule() {
  if (!ruleForm.path_pattern.trim()) return ElMessage.warning('请填写路径模板')
  let body, headers
  try {
    body = parseJson(ruleForm.bodyText, '响应体', {})
    headers = parseJson(ruleForm.headersText, '响应头', null)
  } catch {
    return
  }
  const payload = {
    method: ruleForm.method,
    path_pattern: ruleForm.path_pattern,
    status_code: ruleForm.status_code,
    response_body: body,
    response_headers: headers,
    delay_ms: ruleForm.delay_ms,
    enabled: ruleForm.enabled,
  }
  try {
    if (editingRule.value) await store.updateRule(editingRule.value.id, payload, currentServer.value.id)
    else await store.createRule(currentServer.value.id, payload)
    ruleDialog.value = false
    ElMessage.success('已保存')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '保存失败')
  }
}

async function toggleRule(row, v) {
  await store.updateRule(row.id, { enabled: v }, currentServer.value.id)
}

async function removeRule(row) {
  await ElMessageBox.confirm(`确认删除规则「${row.method} ${row.path_pattern}」？`, '删除', {
    confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
  })
  await store.removeRule(row.id, currentServer.value.id)
  ElMessage.success('已删除')
}

async function runDryRun() {
  dryRunResult.value = await store.dryRun(currentServer.value.id, {
    method: dryRunForm.method,
    path: dryRunForm.path,
  })
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
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.sub-title { font-size: .82rem; color: var(--text-secondary, #909399); margin-top: 4px; }
.mono { font-family: monospace; }
</style>
