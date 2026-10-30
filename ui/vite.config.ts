import path from "node:path"
import { fileURLToPath } from "node:url"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

const currentDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(currentDir, "..")

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, "")
  const backendTarget = env.VITE_BACKEND_URL || "http://127.0.0.1:8000"
  const qualityBackend = env.VITE_QUALITY_BACKEND_URL || backendTarget

  return {
    envDir: repoRoot,
    plugins: [react()],
    server: {
      proxy: {
        "/openapi.json": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/api/quality": {
          target: qualityBackend,
          changeOrigin: true,
        },
        "/api": {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
