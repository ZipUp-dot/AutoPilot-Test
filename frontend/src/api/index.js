import axios from 'axios'
import { ElMessage } from 'element-plus'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1',
  timeout: 30000,
})

apiClient.interceptors.response.use(
  response => {
    const body = response.data
    if (body.code !== undefined && body.code !== 0) {
      ElMessage.error(body.message || '请求失败')
      return Promise.reject(new Error(body.message || '请求失败'))
    }
    return body
  },
  error => {
    const detail = error.response?.data?.detail
    const msg = typeof detail === 'string' ? detail : (error.response?.data?.message || '网络请求失败')
    if (error.response?.status !== 422) {
      ElMessage.error(msg)
    }
    return Promise.reject(error)
  }
)

export default apiClient
