<template>
  <div class="step-editor">
    <div v-for="(step, idx) in steps" :key="idx" class="step-row">
      <span class="step-no">{{ idx + 1 }}</span>

      <el-select
        v-model="step.action"
        size="small"
        style="width: 132px"
        @change="emitChange"
      >
        <el-option v-for="a in ACTIONS" :key="a" :label="a" :value="a" />
      </el-select>

      <el-input
        v-model="step.target"
        size="small"
        placeholder="target（选择器 / URL）"
        @input="emitChange"
      />

      <el-input
        v-model="step.value"
        size="small"
        placeholder="value（可选）"
        @input="emitChange"
      />

      <!-- evidence_ref：只读徽标（8.7：仅名称解析展示，不执行 locator） -->
      <el-tag size="small" type="info" effect="plain" class="evidence-tag">
        {{ step.evidence_ref || '无证据引用' }}
      </el-tag>

      <el-button size="small" :type="matchType(idx)" @click="matchEvidence(idx)">
        在证据元素中查找
      </el-button>

      <el-button size="small" type="danger" text @click="removeStep(idx)">删除</el-button>
    </div>

    <el-button size="small" @click="addStep">新增步骤</el-button>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const ACTIONS = [
  'navigate', 'fill', 'click', 'select', 'hover',
  'assert_text', 'assert_visible', 'screenshot', 'wait', 'swipe', 'back',
]

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  // 证据元素（只读展示源）：[{ selector, name }]
  evidence: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

const steps = ref([])
const matchResult = ref({})   // idx → 'ok' | 'miss'

watch(
  () => props.modelValue,
  (val) => { steps.value = (val || []).map(s => ({ ...s })); matchResult.value = {} },
  { immediate: true, deep: true }
)

function emitChange() {
  emit('update:modelValue', steps.value.map(s => ({ ...s })))
}

function addStep() {
  steps.value.push({
    step_number: steps.value.length + 1,
    action: 'click', target: '', value: '', evidence_ref: '',
  })
  emitChange()
}

function removeStep(idx) {
  steps.value.splice(idx, 1)
  steps.value.forEach((s, i) => { s.step_number = i + 1 })
  emitChange()
}

/** 只读匹配：纯字符串比对（不触碰 DOM / 不执行 locator） */
function matchEvidence(idx) {
  const step = steps.value[idx] || {}
  const needle = String(step.target || step.evidence_ref || '').trim().toLowerCase()
  if (!needle) { matchResult.value = { ...matchResult.value, [idx]: 'miss' }; return }
  const hit = (props.evidence || []).some((el) => {
    const sel = String(el.selector || '').toLowerCase()
    const name = String(el.name || '').toLowerCase()
    return sel === needle || name === needle
  })
  matchResult.value = { ...matchResult.value, [idx]: hit ? 'ok' : 'miss' }
}

function matchType(idx) {
  const r = matchResult.value[idx]
  if (r === 'ok') return 'success'
  if (r === 'miss') return 'danger'
  return 'default'
}
</script>

<style scoped>
.step-editor { display: flex; flex-direction: column; gap: 8px; }
.step-row { display: flex; align-items: center; gap: 8px; }
.step-no { width: 20px; color: var(--text-secondary); font-size: .8rem; }
.evidence-tag { max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
</style>
