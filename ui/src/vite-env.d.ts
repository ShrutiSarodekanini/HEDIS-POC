/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_NAME: string
  readonly VITE_API_BASE_URL: string
  readonly VITE_API_TIMEOUT_MS: string
  readonly VITE_API_OPENAPI_PATH: string
  readonly VITE_DASHBOARD_CATALOG_PATH: string
  readonly VITE_DASHBOARD_SUMMARY_PATH: string
  readonly VITE_DASHBOARD_DEFAULT_MEASURE_ID: string
  readonly VITE_INTEGRATION_SEED_PATH: string
  readonly VITE_INTEGRATION_PROCESS_PATH: string
  readonly VITE_DEFAULT_USER_NAME: string
  readonly VITE_DEFAULT_USER_ROLE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
