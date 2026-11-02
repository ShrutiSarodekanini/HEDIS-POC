const required = (key: keyof ImportMetaEnv): string => {
  const value = import.meta.env[key]
  if (!value || value.trim().length === 0) {
    throw new Error(`Missing required environment variable: ${key}`)
  }
  return value.trim()
}

const asNumber = (value: string, key: string): number => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Environment variable ${key} must be a positive number.`)
  }
  return parsed
}

const normalizeBaseUrl = (value: string): string => value.replace(/\/+$/, "")

const normalizePath = (value: string): string =>
  value.startsWith("/") ? value : `/${value}`

const optional = (key: keyof ImportMetaEnv): string | null => {
  const value = import.meta.env[key]
  if (!value || value.trim().length === 0) {
    return null
  }
  return value.trim()
}

export const envConfig = Object.freeze({
  appName: required("VITE_APP_NAME"),
  apiBaseUrl: normalizeBaseUrl(required("VITE_API_BASE_URL")),
  apiTimeoutMs: asNumber(required("VITE_API_TIMEOUT_MS"), "VITE_API_TIMEOUT_MS"),
  apiOpenApiPath: normalizePath(required("VITE_API_OPENAPI_PATH")),
  dashboardCatalogPath: normalizePath(required("VITE_DASHBOARD_CATALOG_PATH")),
  dashboardSummaryPath: normalizePath(required("VITE_DASHBOARD_SUMMARY_PATH")),
  dashboardDefaultMeasureId: optional("VITE_DASHBOARD_DEFAULT_MEASURE_ID"),
  integrationSeedPath: normalizePath(required("VITE_INTEGRATION_SEED_PATH")),
  integrationProcessPath: normalizePath(required("VITE_INTEGRATION_PROCESS_PATH")),
  defaultUserName: required("VITE_DEFAULT_USER_NAME"),
  defaultUserRole: required("VITE_DEFAULT_USER_ROLE"),
})

export type AppEnvConfig = typeof envConfig
