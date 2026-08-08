<template>
  <el-tag :type="tagType" size="small" effect="plain">{{ label }}</el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ status: { type: String, default: 'pending' } })

const STATUS_MAP = {
  pending:   { type: 'info',    label: '待处理' },
  imported:  { type: '',        label: '已导入' },
  generated: { type: 'success', label: '已生成' },
}

const tagType = computed(() => STATUS_MAP[props.status]?.type || 'info')
const label = computed(() => STATUS_MAP[props.status]?.label || props.status)
</script>
