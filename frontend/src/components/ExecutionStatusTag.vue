<template>
  <el-tag :type="tagType" size="small" :effect="effect">
    <el-icon v-if="spinning" class="spin-icon"><Loading /></el-icon>
    {{ label }}
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ status: { type: String, default: 'running' } })

const STATUS_MAP = {
  queued:    { type: 'info',    label: '排队中' },
  running:   { type: '',      label: '运行中' },
  healing:   { type: 'warning', label: '自愈中' },
  completed: { type: 'success', label: '已完成' },
  stopped:   { type: 'info',    label: '已停止' },
  failed:    { type: 'danger',  label: '失败' },
  interrupted: { type: 'warning', label: '已中断' },
  success:   { type: 'success', label: '成功' },
}

const tagType = computed(() => STATUS_MAP[props.status]?.type || 'info')
const label = computed(() => STATUS_MAP[props.status]?.label || props.status)
const spinning = computed(() => props.status === 'running' || props.status === 'healing')
const effect = computed(() => spinning.value ? 'dark' : 'plain')
</script>

<style scoped>
.spin-icon { display: inline-block; animation: spin 1s linear infinite; margin-right: 4px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
