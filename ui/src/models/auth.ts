export type AuthRole = "admin" | "user"

export interface AuthUser {
  email: string
  role: AuthRole
}

export interface AuthSession {
  token: string
  tokenType: string
  expiresAt: string
  user: AuthUser
}

export interface AdminUserRecord {
  user_id: number
  email: string
  role: AuthRole
  access_status: "pending" | "approved" | "rejected"
  is_active: boolean
  created_at: string
  updated_at: string
}
