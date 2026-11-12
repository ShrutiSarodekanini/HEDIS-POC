import { navigationItems, type NavigationItem } from "../constants/navigation"
import type { AuthRole } from "../models/auth"

export type Permission = "view" | "view_records" | "review" | "calculate" | "admin"

export const AUTH_ROLES: readonly AuthRole[] = ["admin", "analyst", "abstractor", "outreach", "auditor"]

export const ROLE_LABELS: Record<AuthRole, string> = {
  admin: "Administrator",
  analyst: "Quality analyst",
  abstractor: "Abstractor",
  outreach: "Outreach coordinator",
  auditor: "Compliance auditor",
}

/**
 * Which roles have each permission. Mirrors PERMISSIONS in services/integration/auth.py: the API enforces it on every
 * route, and the UI uses it only to hide what a role cannot use.
 */
const PERMISSIONS: Record<Permission, readonly AuthRole[]> = {
  view: AUTH_ROLES,
  view_records: ["admin", "analyst", "abstractor", "auditor"],
  review: ["admin", "analyst", "abstractor"],
  calculate: ["admin", "analyst"],
  admin: ["admin"],
}

export const hasPermission = (role: string | null | undefined, permission: Permission): boolean =>
  (PERMISSIONS[permission] as readonly string[]).includes(role ?? "")

export const isAdmin = (role: string | null | undefined): boolean => hasPermission(role, "admin")

export const roleLabel = (role: string): string => ROLE_LABELS[role as AuthRole] ?? role

// Views missing from the navigation list open for nobody.
export const canOpenView = (view: string, role: string | null | undefined): boolean =>
  navigationItems.some((item) => item.id === view && hasPermission(role, item.permission))

export const visibleNavigationItems = (role: string | null | undefined): NavigationItem[] =>
  navigationItems.filter((item) => canOpenView(item.id, role))
