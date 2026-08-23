<template>
  <div class="element-capture">
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-button
        type="primary"
        :icon="Refresh"
        :loading="crawling"
        @click="handleCrawl"
      >
        {{ projectPlatform === 'android' ? '抓取 Android 元素' : '抓取元素' }}
      </el-button>
    </div>

    <!-- 筛选行 -->
    <div class="filter-row">
      <div class="filter-type">
        <span class="filter-label">类型：</span>
        <el-checkbox-group v-model="filter.types">
          <el-checkbox value="button">按钮 (button)</el-checkbox>
          <el-checkbox value="input">输入框 (input)</el-checkbox>
          <el-checkbox value="link">链接 (link)</el-checkbox>
          <el-checkbox value="select">下拉框 (select)</el-checkbox>
          <el-checkbox value="textarea">文本域 (textarea)</el-checkbox>
        </el-checkbox-group>
      </div>
      <el-input
        v-model="filter.keyword"
        placeholder="搜索文本或选择器"
        clearable
        :prefix-icon="Search"
        style="width: 280px"
      />
    </div>

    <!-- 表格 -->
    <el-table
      v-loading="loading"
      :data="filteredElements"
      border
      stripe
      style="margin-top: 16px"
      highlight-current-row
      @row-click="handleRowClick"
    >
      <el-table-column label="类型 (Type)" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="getTypeTagType(row.element_type)">{{ getTypeLabel(row.element_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="选择器 (Selector)" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="selector-cell">
            <span class="selector-text">{{ row.selector }}</span>
            <el-button link type="primary" size="small" @click.stop="copySelector(row.selector)">
              <el-icon><CopyDocument /></el-icon>
            </el-button>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="文本内容 (Text)" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.text_content || row.text">{{ row.text_content || row.text }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="占位文本 (Placeholder)" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.placeholder">{{ row.placeholder }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态 -->
    <el-empty
      v-if="!loading && elements.length === 0"
      description="暂无元素，请点击「抓取元素」获取页面元素"
    />

    <!-- 分页 -->
    <el-pagination
      v-if="total > 0"
      v-model:current-page="pagination.page"
      v-model:page-size="pagination.size"
      :total="total"
      :page-sizes="[50]"
      layout="total, prev, pager, next"
      style="margin-top: 16px; justify-content: flex-end"
      @current-change="fetchList"
    />

    <!-- 详情抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      title="元素详情"
      direction="rtl"
      size="480px"
    >
      <template v-if="currentElement">
        <div class="drawer-actions">
          <el-button type="primary" size="small" @click="copySelector(currentElement.selector)">
            <el-icon><CopyDocument /></el-icon>
            复制选择器
          </el-button>
        </div>

        <el-descriptions :column="1" border style="margin-top: 16px">
          <el-descriptions-item label="标签 (Tag)">
            {{ currentElement.tag_name || currentElement.tag || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="类型 (Type)">
            <el-tag size="small" :type="getTypeTagType(currentElement.element_type)">
              {{ getTypeLabel(currentElement.element_type) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="选择器 (Selector)">
            {{ currentElement.selector || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="文本内容 (Text)">
            {{ currentElement.text_content || currentElement.text || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="占位文本 (Placeholder)">
            {{ currentElement.placeholder || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="ID">
            {{ currentElement.element_id || currentElement.id || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="Class">
            {{ currentElement.class_name || '—' }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 属性键值对 -->
        <div v-if="attributesEntries.length > 0" style="margin-top: 20px">
          <h4 class="section-title">属性 (Attributes)</h4>
          <el-descriptions :column="1" border>
            <el-descriptions-item
              v-for="(value, key) in attributesEntries"
              :key="key"
              :label="String(key)"
            >
              {{ String(value) }}
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 边界框 -->
        <div v-if="currentElement.bounding_box" style="margin-top: 20px">
          <h4 class="section-title">边界框 (Bounding Box)</h4>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="x">{{ currentElement.bounding_box.x }}</el-descriptions-item>
            <el-descriptions-item label="y">{{ currentElement.bounding_box.y }}</el-descriptions-item>
            <el-descriptions-item label="width">{{ currentElement.bounding_box.width }}</el-descriptions-item>
            <el-descriptions-item label="height">{{ currentElement.bounding_box.height }}</el-descriptions-item>
          </el-descriptions>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, CopyDocument, Search } from '@element-plus/icons-vue'
import { elementApi } from '@/api/element'
import { useProjectStore } from '@/stores/projectStore'

const route = useRoute()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.id)
const projectPlatform = computed(() => projectStore.currentProject?.platform || 'web')

const elements = ref([])
const loading = ref(false)
const crawling = ref(false)
const total = ref(0)
const pagination = reactive({ page: 1, size: 50 })

const filter = reactive({
  types: [],
  keyword: '',
})

const drawerVisible = ref(false)
const currentElement = ref(null)

const debouncedKeyword = ref('')
let debounceTimer = null
watch(
  () => filter.keyword,
  (val) => {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      debouncedKeyword.value = val
    }, 300)
  }
)

const filteredElements = computed(() => {
  let result = elements.value
  if (filter.types.length > 0) {
    result = result.filter((el) => filter.types.includes(el.element_type))
  }
  if (debouncedKeyword.value) {
    const kw = debouncedKeyword.value.toLowerCase()
    result = result.filter((el) => {
      const text = (el.text_content || el.text || '').toLowerCase()
      const selector = (el.selector || '').toLowerCase()
      const placeholder = (el.placeholder || '').toLowerCase()
      return text.includes(kw) || selector.includes(kw) || placeholder.includes(kw)
    })
  }
  return result
})

const attributesEntries = computed(() => {
  if (!currentElement.value?.attributes) return []
  const attrs = currentElement.value.attributes
  if (typeof attrs === 'object') return attrs
  try {
    return JSON.parse(attrs)
  } catch {
    return []
  }
})

function getTypeTagType(type) {
  const map = {
    button: 'primary',
    input: 'success',
    link: 'warning',
    select: 'danger',
    textarea: 'info',
  }
  return map[type] || 'info'
}

function getTypeLabel(type) {
  const map = {
    button: '按钮 (button)',
    input: '输入框 (input)',
    link: '链接 (link)',
    select: '下拉框 (select)',
    textarea: '文本域 (textarea)',
  }
  return map[type] || type
}

async function fetchList() {
  loading.value = true
  try {
    const res = await elementApi.list(projectId.value, {
      page: pagination.page,
      size: pagination.size,
    })
    const data = res.data || res
    elements.value = data.items || data.data || []
    total.value = data.total || 0
  } catch {
    // 错误由拦截器统一处理
  } finally {
    loading.value = false
  }
}

async function handleCrawl() {
  crawling.value = true
  try {
    if (projectPlatform.value === 'android') {
      const config = projectStore.currentProject?.config_json || {}
      await elementApi.crawlAndroid(projectId.value, config)
    } else {
      await elementApi.crawl(projectId.value, 1)
    }
    ElMessage.success('抓取完成')
    pagination.page = 1
    await fetchList()
  } catch {
    // 错误由拦截器统一处理
  } finally {
    crawling.value = false
  }
}

function copySelector(selector) {
  if (!selector) return
  navigator.clipboard.writeText(selector).then(() => {
    ElMessage.success('选择器已复制到剪贴板')
  })
}

function handleRowClick(row) {
  currentElement.value = row
  drawerVisible.value = true
}

onMounted(() => {
  fetchList()
})
</script>

<style scoped>
.element-capture {
  padding: 4px 0;
}

.toolbar {
  display: flex;
  gap: 12px;
}

.filter-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
  gap: 16px;
  flex-wrap: wrap;
}

.filter-type {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.filter-label {
  font-size: 0.9rem;
  color: var(--text-secondary, #909399);
  white-space: nowrap;
}

.selector-cell {
  display: flex;
  align-items: center;
  gap: 4px;
}

.selector-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-muted {
  color: #c0c4cc;
}

.drawer-actions {
  display: flex;
  gap: 8px;
}

.section-title {
  margin: 0 0 8px 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text-primary, #303133);
}
</style>
