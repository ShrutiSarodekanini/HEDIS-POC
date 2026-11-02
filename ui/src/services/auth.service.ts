import type { AdminUserRecord, AuthRole, AuthSession, AuthUser } from "../models/auth"
import { envConfig } from "../config/env"
import { fetchJson, postJson, putJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

interface SignInResponse {
  token: string
  token_type: string
  expires_at: string
  user: AuthUser
}

interface SignUpResponse {
  status: string
  message: string
}

interface AdminUsersResponse {
  users: AdminUserRecord[]
}

export interface AuthCapabilities {
  authEnabled: boolean
  adminEnabled: boolean
}

export async function discoverAuthCapabilities(): Promise<AuthCapabilities> {
  const paths = await getOpenApiPaths(envConfig.apiTimeoutMs)
  const authEnabled = hasOpenApiPath(paths, "/api/auth/signin")
    && hasOpenApiPath(paths, "/api/auth/signup")
    && hasOpenApiPath(paths, "/api/auth/me")

  return {
    authEnabled,
    adminEnabled: authEnabled && hasOpenApiPath(paths, "/api/admin/users"),
  }
}

export async function signIn(email: string, password: string): Promise<AuthSession> {
  const response = await postJson<SignInResponse>(
    withApiBase("/auth/signin"),
    envConfig.apiTimeoutMs,
    { email, password },
  )

  return {
    token: response.token,
    tokenType: response.token_type,
    expiresAt: response.expires_at,
    user: response.user,
  }
}

export async function signUp(email: string, password: string): Promise<SignUpResponse> {
  return postJson<SignUpResponse>(
    withApiBase("/auth/signup"),
    envConfig.apiTimeoutMs,
    { email, password },
  )
}

export async function getCurrentUser(): Promise<AuthUser> {
  return fetchJson<AuthUser>(withApiBase("/auth/me"), envConfig.apiTimeoutMs)
}

export async function signOut(): Promise<void> {
  await postJson<{ status: string }>(withApiBase("/auth/signout"), envConfig.apiTimeoutMs, {})
}

export async function listAdminUsers(): Promise<AdminUserRecord[]> {
  const response = await fetchJson<AdminUsersResponse>(withApiBase("/admin/users"), envConfig.apiTimeoutMs)
  return response.users
}

export async function updateUserAccess(
  userId: number,
  accessStatus: "approved" | "rejected",
): Promise<AdminUserRecord> {
  return putJson<AdminUserRecord>(
    withApiBase(`/admin/users/${userId}/access`),
    envConfig.apiTimeoutMs,
    { access_status: accessStatus },
  )
}

export async function updateUserRole(userId: number, role: AuthRole): Promise<AdminUserRecord> {
  return putJson<AdminUserRecord>(
    withApiBase(`/admin/users/${userId}/role`),
    envConfig.apiTimeoutMs,
    { role },
  )
}
