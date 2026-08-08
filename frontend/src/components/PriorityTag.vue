<template>
  <el-tag :type="tagType" size="small" effect="plain">{{ priority }}</el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ priority: { type: String, default: 'P1' } })

const PRIORITY_MAP = { P0: 'danger', P1: 'warning', P2: 'success', P3: 'info' }
const tagType = computed(() => PRIORITY_MAP[props.priority] || 'info')
</script>
