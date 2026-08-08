<template>
  <div>
    <el-card v-if="!reports.length" style="text-align:center;padding:40px">
      <el-icon :size="48" color="#ccc"><Document /></el-icon>
      <p style="color:var(--text-secondary);margin-top:12px">暂无报告。完成一次执行后，报告将自动生成。</p>
    </el-card>

    <el-row v-else :gutter="16">
      <el-col v-for="r in reports" :key="r.report_id" :xs="24" :sm="12" :md="8" style="margin-bottom:16px">
        <el-card shadow="hover">
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span style="font-weight:600">{{ r.batch_name || `执行 #${r.execution_id}` }}</span>
              <span :style="{ color: r.passRate >= 90 ? '#67c23a' : r.passRate >= 60 ? '#e6a23c' : '#f56c6c', fontWeight: 700 }">
                {{ r.passRate }}%
              </span>
            </div>
          </template>
          <p style="font-size:.85rem;color:var(--text-secondary);margin-bottom:8px">{{ r.generatedTime }}</p>
          <p style="font-size:.82rem;margin-bottom:8px">用例数: {{ r.totalCases }} | 通过: {{ r.passed }} | 失败: {{ r.failed }}</p>
          <el-space>
            <el-button size="small" @click="openReport(r)">查看</el-button>
            <el-button size="small" type="primary" @click="downloadReport(r)">下载</el-button>
          </el-space>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="showIframe" fullscreen :destroy-on-close="true">
      <template #header>
        <div style="display:flex;gap:8px;align-items:center">
          <span style="flex:1">{{ iframeTitle }}</span>
          <el-button size="small" @click="downloadIframe">下载</el-button>
          <el-button size="small" @click="printIframe">打印</el-button>
          <el-button size="small" @click="showIframe = false">关闭</el-button>
        </div>
      </template>
      <iframe v-if="iframeSrc" :src="iframeSrc" style="width:100%;height:calc(100vh - 120px);border:none" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { executionApi } from '@/api/execution'
import { reportApi } from '@/api/report'

const route = useRoute()
const projectId = ref(Number(route.params.id))
const reports = ref([])
const showIframe = ref(false)
const iframeSrc = ref('')
const iframeTitle = ref('')
let activeIframe = null

onMounted(async () => {
  try {
    const execRes = await executionApi.list(projectId.value)
    const items = execRes.data?.items || []
    const reportList = []
    for (const e of items) {
      if (e.status !== 'completed') continue
      try {
        const repRes = await reportApi.getInfo(e.id)
        if (repRes.data?.download_url) {
          const s = repRes.data.summary || {}
          reportList.push({
            report_id: repRes.data.report_id,
            execution_id: e.id,
            batch_name: e.batch_name,
            totalCases: s.total_cases || e.total_cases,
            passed: s.passed || e.passed_cases,
            failed: s.failed || e.failed_cases,
            passRate: s.pass_rate || (e.total_cases ? Math.round(e.passed_cases / e.total_cases * 100) : 0),
            generatedTime: repRes.data.created_at || '',
            download_url: repRes.data.download_url,
          })
        }
      } catch (e) { /* pass */ }
    }
    reports.value = reportList
  } catch (e) { /* pass */ }
})

function openReport(r) {
  iframeSrc.value = r.download_url
  iframeTitle.value = r.batch_name || `执行 #${r.execution_id}`
  showIframe.value = true
}

function downloadReport(r) {
  window.open(r.download_url, '_blank')
}

function downloadIframe() { window.open(iframeSrc.value, '_blank') }

function printIframe() {
  const iframe = document.querySelector('.el-dialog iframe')
  if (iframe) { try { iframe.contentWindow.print() } catch (e) { window.open(iframeSrc.value, '_blank') } }
}
</script>
