import type { AuthSession, AuthUser } from "../models/auth"

const AUTH_SESSION_STORAGE_KEY = "hedis.ui.auth.session"

const hasLocalStorage = (): boolean => typeof window !== "undefined" && typeof window.localStorage !== "undefined"

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null

const toSession = (value: unknown): AuthSession | null => {
  if (!isObject(value) || !isObject(value.user)) {
    return null
  }

  const token = value.token
  const tokenType = value.tokenType
  const expiresAt = value.expiresAt
  const email = value.user.email
  const role = value.user.role

  if (typeof token !== "string" || token.trim().length === 0) {
    return null
  }
  if (typeof tokenType !== "string" || tokenType.trim().length === 0) {
    return null
  }
  if (typeof expiresAt !== "string" || expiresAt.trim().length === 0) {
    return null
  }
  if (typeof email !== "string" || email.trim().length === 0) {
    return null
  }
  if (role !== "admin" && role !== "user") {
    return null
  }

  return {
    token,
    tokenType,
    expiresAt,
    user: {
      email,
      role,
    },
  }
}

export function loadAuthSession(): AuthSession | null {
  if (!hasLocalStorage()) {
    return null
  }

  const raw = window.localStorage.getItem(AUTH_SESSION_STORAGE_KEY)
  if (!raw) {
    return null
  }

  try {
    return toSession(JSON.parse(raw))
  } catch {
    return null
  }
}

export function saveAuthSession(session: AuthSession): void {
  if (!hasLocalStorage()) {
    return
  }
  window.localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(session))
}

export function clearAuthSession(): void {
  if (!hasLocalStorage()) {
    return
  }
  window.localStorage.removeItem(AUTH_SESSION_STORAGE_KEY)
}

export function getAuthToken(): string | null {
  return loadAuthSession()?.token ?? null
}

export function updateAuthSessionUser(user: AuthUser): void {
  const session = loadAuthSession()
  if (!session) {
    return
  }
  saveAuthSession({
    ...session,
    user,
  })
}
