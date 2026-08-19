import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Fail loudly instead of silently moving to another port, which would break
    // the backend's CORS allowlist.
    strictPort: true,
  },
  build: {
    outDir: 'dist',
  },
  // Relative asset paths so the built bundle also loads from file:// when this
  // is later wrapped in Tauri or Electron.
  base: './',
})
