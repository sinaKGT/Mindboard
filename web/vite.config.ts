import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Builds straight into the Python package, so the wheel ships the interface
// prebuilt and the user never needs Node to run Mindboard.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/mindboard/web', emptyOutDir: true, assetsDir: 'assets' },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:7788' },
  },
})
