import { envConfig } from "../config/env"
import { fetchJson } from "./http"

interface OpenApiDocument {
  paths?: Record<string, unknown>
}

const OPENAPI_CACHE_TTL_MS = 60_000

let cachedPaths: Record<string, unknown> | null = null
let cacheExpiresAt = 0
let inFlight: Promise<Record<string, unknown>> | null = null

const now = (): number => Date.now()

export const hasOpenApiPath = (paths: Record<string, unknown>, path: string): boolean =>
  Object.prototype.hasOwnProperty.call(paths, path)

export async function getOpenApiPaths(timeoutMs: number): Promise<Record<string, unknown>> {
  if (cachedPaths && cacheExpiresAt > now()) {
    return cachedPaths
  }

  if (inFlight) {
    return inFlight
  }

  inFlight = fetchJson<OpenApiDocument>(envConfig.apiOpenApiPath, timeoutMs)
    .then((openApi) => {
      const paths = openApi.paths ?? {}
      cachedPaths = paths
      cacheExpiresAt = now() + OPENAPI_CACHE_TTL_MS
      return paths
    })
    .finally(() => {
      inFlight = null
    })

  return inFlight
}

export function clearOpenApiCache(): void {
  cachedPaths = null
  cacheExpiresAt = 0
  inFlight = null
}
