import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../orchestrator/web/dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8093',
        configure: (proxy) => {
          proxy.on('error', (err) => {
            console.warn('[vite] api proxy error (non-fatal):', err.message)
          })
        },
      },
      '/ws': {
        target: 'ws://127.0.0.1:8093',
        ws: true,
        configure: (proxy) => {
          proxy.on('error', (err) => {
            console.warn('[vite] ws proxy error (non-fatal):', err.message)
          })
        },
      },
    },
  },
})
