import type { Permission } from "../utils/access"

export interface NavigationItem {
  id: string
  label: string
  status: "active" | "coming-soon"
  /** The permission a role needs to see this view (utils/access.ts). */
  permission: Permission
}

export const navigationItems: NavigationItem[] = [
  { id: "dashboard", label: "Dashboard", status: "active", permission: "view" },
  { id: "ingestion", label: "Run Ingestion", status: "active", permission: "view_records" },
  { id: "calculation", label: "Run Gap Calculation", status: "active", permission: "calculate" },
  { id: "quality", label: "Data Quality", status: "active", permission: "view" },
  { id: "stars", label: "Star Rating", status: "active", permission: "view" },
  { id: "prioritization", label: "Prioritization", status: "active", permission: "view" },
  { id: "evidence", label: "Evidence Review", status: "active", permission: "view_records" },
  { id: "admin", label: "Administrator", status: "active", permission: "admin" },
]
