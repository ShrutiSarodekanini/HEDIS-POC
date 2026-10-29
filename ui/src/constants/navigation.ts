export interface NavigationItem {
  id: string
  label: string
  status: "active" | "coming-soon"
}

export const navigationItems: NavigationItem[] = [
  { id: "dashboard", label: "Dashboard", status: "active" },
  { id: "ingestion", label: "Run Ingestion", status: "active" },
  { id: "calculation", label: "Run Gap Calculation", status: "active" },
  { id: "quality", label: "Data Quality", status: "active" },
  { id: "stars", label: "Star Rating", status: "active" },
  { id: "prioritization", label: "Prioritization", status: "active" },
  { id: "evidence", label: "Evidence Review", status: "active" },
]
