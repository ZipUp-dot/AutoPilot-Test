import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 内部令牌：与后端 INTERNAL_API_TOKEN 一致（开发模式通过 VITE/进程环境变量注入；
// 生产环境由 nginx 反代附加 Authorization 头，前端不接触令牌）
const INTERNAL_API_TOKEN = process.env.INTERNAL_API_TOKEN || ''

function withFileAuth(target) {
  return {
    target,
    changeOrigin: true,
    // 文件访问接口需要 Bearer 令牌：开发模式由 vite 代理附加
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq) => {
        if (INTERNAL_API_TOKEN) {
          proxyReq.setHeader('Authorization', `Bearer ${INTERNAL_API_TOKEN}`)
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': '/src' },
  },
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/reports': withFileAuth('http://127.0.0.1:8000'),
      '/uploads': withFileAuth('http://127.0.0.1:8000'),
    },
  },
})
