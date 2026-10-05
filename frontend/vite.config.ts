import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { readFileSync } from 'node:fs'

// 版本号单一来源是仓库根目录的 VERSION，构建时注入 __APP_VERSION__；
// 与后端 /api/health、/api/info 返回的版本保持一致
function readAppVersion(): string {
  try {
    return readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim() || '0.0.0'
  } catch {
    return '0.0.0'
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  define: {
    __APP_VERSION__: JSON.stringify(readAppVersion()),
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8787',
    },
  },
})
