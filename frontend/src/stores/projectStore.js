import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { projectApi } from '@/api/project'

export const useProjectStore = defineStore('project', () => {
  const projects = ref([])
  const currentProject = ref(null)
  const loading = ref(false)

  const projectOptions = computed(() =>
    projects.value.map(p => ({ label: p.name, value: p.id }))
  )

  async function fetchProjects() {
    loading.value = true
    try {
      const res = await projectApi.list(1, 100)
      projects.value = res.data?.items || []
    } finally { loading.value = false }
  }

  async function createProject(data) {
    const res = await projectApi.create(data)
    return res.data
  }

  async function updateProject(id, data) {
    await projectApi.update(id, data)
  }

  async function deleteProject(id) {
    await projectApi.delete(id)
  }

  function setCurrentProject(project) {
    currentProject.value = project
  }

  return { projects, currentProject, loading, projectOptions, fetchProjects, createProject, updateProject, deleteProject, setCurrentProject }
})
