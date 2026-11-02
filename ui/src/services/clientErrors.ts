// Sends browser errors to the backend (POST /api/client-errors) for Grafana. Fire and forget: it never throws, never
// waits, and never sends request or response bodies, tokens or form values; ids in paths become {id}.

type ClientErrorKind = "js_error" | "unhandled_rejection" | "api_error"

interface ClientErrorReport {
  kind: ClientErrorKind
  apiPath?: string
  status?: number
  message?: string
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/+$/, "")
const endpoint = `${apiBase}/client-errors`
const maxPerMinute = 20
const sentAt: number[] = []
const uuidSegment = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const pathTemplate = (url: string): string => {
  const path = url.replace(/^[a-z]+:\/\/[^/]+/i, "").split(/[?#]/)[0]
  return path.split("/").map((part) => (/^\d+$/.test(part) || uuidSegment.test(part) ? "{id}" : part)).join("/")
}

const withinLimit = (): boolean => {
  const now = Date.now()
  while (sentAt.length > 0 && now - sentAt[0] > 60_000) {
    sentAt.shift()
  }
  if (sentAt.length >= maxPerMinute) {
    return false
  }
  sentAt.push(now)
  return true
}

export function reportClientError(report: ClientErrorReport): void {
  try {
    if (report.apiPath?.includes("/client-errors") || !withinLimit()) {
      return
    }
    const body = JSON.stringify({
      kind: report.kind,
      page: window.location.hash || "#view-dashboard",
      api_path: report.apiPath ? pathTemplate(report.apiPath).slice(0, 300) : null,
      status: report.status ?? null,
      message: (report.message ?? "").slice(0, 300),
    })
    if (typeof navigator.sendBeacon === "function"
      && navigator.sendBeacon(endpoint, new Blob([body], { type: "application/json" }))) {
      return
    }
    void fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true })
      .catch(() => undefined)
  } catch {
    // Reporting must never break the page.
  }
}

const errorMessage = (value: unknown): string => {
  if (value instanceof Error) {
    return `${value.name}: ${value.message}`
  }
  return typeof value === "string" ? value : "non-Error value"
}

export function reportApiFailure(url: string, status: number, error?: unknown): void {
  reportClientError({
    kind: "api_error",
    apiPath: url,
    status,
    message: error === undefined ? `HTTP ${status}` : errorMessage(error),
  })
}

export function installClientErrorReporter(): void {
  try {
    window.addEventListener("error", (event: ErrorEvent) => {
      reportClientError({ kind: "js_error", message: event.message || errorMessage(event.error) })
    })
    window.addEventListener("unhandledrejection", (event: PromiseRejectionEvent) => {
      reportClientError({ kind: "unhandled_rejection", message: errorMessage(event.reason) })
    })
  } catch {
    // Reporting must never break the page.
  }
}
