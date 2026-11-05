export interface NavigationItem {
  id: string
  label: string
  status: "active" | "coming-soon"
  adminOnly: boolean
}

export const navigationItems: NavigationItem[] = [
  { id: "dashboard", label: "Dashboard", status: "active", adminOnly: false },
  { id: "ingestion", label: "Run Ingestion", status: "active", adminOnly: false },
  { id: "calculation", label: "Run Gap Calculation", status: "active", adminOnly: true },
  { id: "quality", label: "Data Quality", status: "active", adminOnly: true },
  { id: "stars", label: "Star Rating", status: "active", adminOnly: true },
  { id: "prioritization", label: "Prioritization", status: "active", adminOnly: true },
  { id: "evidence", label: "Evidence Review", status: "active", adminOnly: true },
  { id: "admin", label: "Administrator", status: "active", adminOnly: true },
]
