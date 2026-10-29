import { envConfig } from "../config/env"
import type {
  IntegrationActionResult,
  IntegrationCapabilities,
} from "../models/integration"
import {
  HttpError,
  postJson,
  toRequestErrorMessage,
} from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

export async function fetchIntegrationCapabilities(): Promise<IntegrationCapabilities> {
  try {
    const paths = await getOpenApiPaths(envConfig.apiTimeoutMs)

    return {
      hasSeedEndpoint: hasOpenApiPath(paths, withApiBase(envConfig.integrationSeedPath)),
      hasProcessEndpoint: hasOpenApiPath(paths, withApiBase(envConfig.integrationProcessPath)),
    }
  } catch {
    return {
      hasSeedEndpoint: false,
      hasProcessEndpoint: false,
    }
  }
}

async function runAction(
  action: "seed" | "process",
  path: string,
): Promise<IntegrationActionResult> {
  try {
    await postJson<unknown>(withApiBase(path), envConfig.apiTimeoutMs, {})
    return {
      action,
      success: true,
      statusCode: 200,
      message: "Completed successfully.",
      completedAt: new Date().toISOString(),
    }
  } catch (error) {
    return {
      action,
      success: false,
      statusCode: error instanceof HttpError ? error.status : null,
      message: toRequestErrorMessage(error),
      completedAt: new Date().toISOString(),
    }
  }
}

export async function runSeedDataAction(): Promise<IntegrationActionResult> {
  return runAction("seed", envConfig.integrationSeedPath)
}

export async function runProcessGapsAction(): Promise<IntegrationActionResult> {
  return runAction("process", envConfig.integrationProcessPath)
}
