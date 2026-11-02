export interface IntegrationCapabilities {
  hasSeedEndpoint: boolean
  hasProcessEndpoint: boolean
}

export interface IntegrationActionResult {
  action: "seed" | "process"
  success: boolean
  statusCode: number | null
  message: string
  completedAt: string
}
