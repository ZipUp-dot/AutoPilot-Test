<template>
  <div class="code-preview">
    <div class="code-toolbar">
      <el-button link size="small" @click="collapsed = !collapsed">
        {{ collapsed ? '展开' : '折叠' }}
      </el-button>
      <el-button link size="small" @click="copyCode">复制代码</el-button>
    </div>
    <div v-show="!collapsed" class="code-body">
      <table class="code-table">
        <tr v-for="(line, i) in lines" :key="i">
          <td class="line-num">{{ i + 1 }}</td>
          <td class="line-content" v-html="highlightLine(line)" />
        </tr>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({ code: { type: String, default: '' }, maxLines: { type: Number, default: 0 } })

const collapsed = ref(!!props.maxLines)

const lines = computed(() => {
  const arr = (props.code || '').split('\n')
  return arr
})

function highlightLine(line) {
  let html = escapeHtml(line)
  html = html.replace(/\b(async|await|def|return|import|from|try|except|if|else|for|in|class|pass|raise|with|as|True|False|None|not|and|or|is|lambda)\b/g,
    '<span class="kw">$1</span>')
  html = html.replace(/(#.*$)/gm, '<span class="cm">$1</span>')
  html = html.replace(/(&quot;[^&]*&quot;|&#39;[^&]*&#39;)/g, '<span class="st">$1</span>')
  return html
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

async function copyCode() {
  try {
    await navigator.clipboard.writeText(props.code)
    ElMessage.success('已复制')
  } catch { ElMessage.error('复制失败') }
}
</script>

<style scoped>
.code-preview { border-radius: 6px; overflow: hidden; background: #1e1e2e; }
.code-toolbar { display: flex; justify-content: flex-end; gap: 4px; padding: 4px 8px; background: #2d2d3f; border-bottom: 1px solid #3d3d4f; }
.code-table { width: 100%; border-collapse: collapse; font-family: 'SF Mono', Consolas, 'Cascadia Code', monospace; font-size: 0.78rem; line-height: 1.55; }
.line-num { width: 44px; text-align: right; padding: 0 10px; color: #6c7086; user-select: none; border-right: 1px solid #3d3d4f; vertical-align: top; }
.line-content { padding: 0 12px; color: #cdd6f4; white-space: pre; }
.line-content :deep(.kw) { color: #cba6f7; font-weight: 600; }
.line-content :deep(.cm) { color: #6c7086; }
.line-content :deep(.st) { color: #a6e3a1; }
.code-body { max-height: 420px; overflow: auto; }
</style>
