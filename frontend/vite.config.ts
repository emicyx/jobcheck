import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // '::' 双栈监听：同时接受 localhost(::1) 与 127.0.0.1 的访问，
    // 否则 Windows 上只绑 ::1，用 127.0.0.1 打开平台就是「无法访问此网站」
    host: '::',
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
})
