import type { NavigationItem } from "../../constants/navigation"

interface SidebarProps {
  items: NavigationItem[]
  activeId: string
}

const hrefById: Record<string, string> = {
  dashboard: "#view-dashboard",
  ingestion: "#view-ingestion",
  calculation: "#view-calculation",
  quality: "#view-quality",
  stars: "#view-stars",
  prioritization: "#view-prioritization",
  evidence: "#view-evidence",
  admin: "#view-admin",
}

export function Sidebar({ items, activeId }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Main navigation">
      <nav className="menu">
        {items.map((item) => (
          <a
            key={item.id}
            href={hrefById[item.id] ?? "#"}
            className={item.id === activeId ? "active" : ""}
            aria-current={item.id === activeId ? "page" : undefined}
            onClick={(event) => {
              if (item.status === "coming-soon") {
                event.preventDefault()
              }
            }}
          >
            <span>{item.label}</span>
            {item.status === "coming-soon" ? (
              <small className="menu-tag">Soon</small>
            ) : null}
          </a>
        ))}
      </nav>

      <div className="side-note">
        <small>Vertical Use Case HC-02</small>
        <small>KANINI</small>
      </div>
    </aside>
  )
}
