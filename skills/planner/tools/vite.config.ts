import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import plannerApi from './server/vitePlugin.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), plannerApi()],
})
