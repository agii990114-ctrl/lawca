import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 개발 중에는 /api 요청을 FastAPI(기본 localhost:8000)로 넘긴다. LAWCA_API_URL로 바꿀 수 있다.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': process.env.LAWCA_API_URL ?? 'http://localhost:8000',
    },
  },
})
