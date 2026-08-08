<template>
  <div>
    <div class="page-header">
      <h2>项目管理</h2>
      <el-button type="primary" @click="showCreate = true">新建项目</el-button>
    </div>

    <el-table :data="projectStore.projects" v-loading="projectStore.loading" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="项目名称" />
      <el-table-column prop="target_url" label="目标地址" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push(`/projects/${row.id}`)">详情</el-button>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" :title="editingId ? '编辑项目' : '新建项目'" width="520px" @closed="resetForm">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="项目名称" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="目标地址" prop="target_url">
          <el-input v-model="form.target_url" placeholder="https://example.com" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/projectStore'
import { ElMessage, ElMessageBox } from 'element-plus'

const router = useRouter()
const projectStore = useProjectStore()
const showCreate = ref(false)
const editingId = ref(null)
const formRef = ref(null)

const URL_PATTERN = /^https?:\/\/.+/

const rules = {
  name: [
    { required: true, message: '名称不能为空', trigger: 'blur' },
    { max: 100, message: '名称不能超过100个字符', trigger: 'blur' },
  ],
  target_url: [
    { required: true, message: '目标地址不能为空', trigger: 'blur' },
    { pattern: URL_PATTERN, message: '请输入合法的 URL 格式（http:// 或 https://）', trigger: 'blur' },
  ],
}

const form = reactive({ name: '', target_url: '' })

function resetForm() {
  editingId.value = null
  form.name = ''
  form.target_url = ''
  formRef.value?.resetFields()
}

onMounted(() => projectStore.fetchProjects())

async function handleSubmit() {
  await formRef.value.validate()
  if (editingId.value) {
    await projectStore.updateProject(editingId.value, { ...form })
    ElMessage.success('更新成功')
  } else {
    await projectStore.createProject({ ...form })
    ElMessage.success('创建成功')
  }
  showCreate.value = false
  resetForm()
  projectStore.fetchProjects()
}

function openEdit(row) {
  editingId.value = row.id
  form.name = row.name
  form.target_url = row.target_url
  showCreate.value = true
}

async function handleDelete(id) {
  await ElMessageBox.confirm('确认删除该项目？所有关联数据将被清空。')
  await projectStore.deleteProject(id)
  ElMessage.success('已删除')
  projectStore.fetchProjects()
}
</script>
