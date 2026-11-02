import { getAuthToken } from "./authSession"

export class HttpError extends Error {
  readonly status: number

  readonly payload: unknown

  constructor(status: number, payload: unknown) {
    super(`Request failed with status ${status}`)
    this.status = status
    this.payload = payload
  }
}

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== "object") {
    return null
  }
  return value as Record<string, unknown>
}

const payloadDetail = (payload: unknown): string | null => {
  const record = asRecord(payload)
  if (!record) {
    return null
  }

  const detail = record.detail
  if (typeof detail === "string" && detail.trim().length > 0) {
    return detail
  }

  return null
}

export const toRequestErrorMessage = (error: unknown): string => {
  if (error instanceof HttpError) {
    const detail = payloadDetail(error.payload)
    if (detail) {
      return detail
    }

    if (error.status === 502) {
      return "Backend is unreachable from the Vite proxy (502 Bad Gateway)."
    }

    return `Backend request failed (${error.status}).`
  }

  if (error instanceof DOMException && error.name === "AbortError") {
    return "Backend request timed out."
  }

  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message
  }

  return "Unknown backend request error."
}

const withAuthHeaders = (headers: Record<string, string>): Record<string, string> => {
  const token = getAuthToken()
  if (!token) {
    return headers
  }

  return {
    ...headers,
    Authorization: `Bearer ${token}`,
  }
}

export async function fetchJson<T>(url: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      method: "GET",
      headers: withAuthHeaders({
        Accept: "application/json",
      }),
      signal: controller.signal,
    })

    const payload = (await response.json().catch(() => null)) as unknown

    if (!response.ok) {
      throw new HttpError(response.status, payload)
    }

    return payload as T
  } finally {
    window.clearTimeout(timer)
  }
}

export async function postJson<T>(
  url: string,
  timeoutMs: number,
  body: unknown,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: withAuthHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify(body),
      signal: controller.signal,
    })

    const payload = (await response.json().catch(() => null)) as unknown

    if (!response.ok) {
      throw new HttpError(response.status, payload)
    }

    return payload as T
  } finally {
    window.clearTimeout(timer)
  }
}

export async function postForm<T>(
  url: string,
  timeoutMs: number,
  formData: FormData,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: withAuthHeaders({
        Accept: "application/json",
      }),
      body: formData,
      signal: controller.signal,
    })

    const payload = (await response.json().catch(() => null)) as unknown

    if (!response.ok) {
      throw new HttpError(response.status, payload)
    }

    return payload as T
  } finally {
    window.clearTimeout(timer)
  }
}

export async function putJson<T>(
  url: string,
  timeoutMs: number,
  body: unknown,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      method: "PUT",
      headers: withAuthHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify(body),
      signal: controller.signal,
    })

    const payload = (await response.json().catch(() => null)) as unknown

    if (!response.ok) {
      throw new HttpError(response.status, payload)
    }

    return payload as T
  } finally {
    window.clearTimeout(timer)
  }
}
