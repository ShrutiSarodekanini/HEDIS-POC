import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const qualityBackend = env.VITE_QUALITY_BACKEND_URL || env.VITE_BACKEND_URL

  return {
    plugins: [react()],
    server: {
      proxy: {
        "/openapi.json": {
          target: env.VITE_BACKEND_URL,
          changeOrigin: true,
        },
        "/api/quality": {
          target: qualityBackend,
          changeOrigin: true,
        },
        "/api": {
          target: env.VITE_BACKEND_URL,
          changeOrigin: true,
        },
      },
    },
  }
})
