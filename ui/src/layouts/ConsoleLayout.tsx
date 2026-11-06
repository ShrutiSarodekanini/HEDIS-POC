import type { ReactNode } from "react"

import type { NavigationItem } from "../constants/navigation"
import { Sidebar } from "../components/layout/Sidebar"
import { TopBar } from "../components/layout/TopBar"
import { useSidebarState } from "../hooks/useSidebarState"

const SIDEBAR_ID = "app-sidebar"

interface ConsoleLayoutProps {
  children: ReactNode
  appName: string
  runLabel: string
  runMeta: string
  userName: string
  userRole: string
  navItems: NavigationItem[]
  activeNavId: string
  onLogout?: () => void
}

export function ConsoleLayout({
  children,
  appName,
  runLabel,
  runMeta,
  userName,
  userRole,
  navItems,
  activeNavId,
  onLogout,
}: ConsoleLayoutProps) {
  const sidebar = useSidebarState()

  return (
    <div className={sidebar.open ? "shell sidebar-open" : "shell sidebar-closed"}>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <TopBar
        appName={appName}
        runLabel={runLabel}
        runMeta={runMeta}
        userName={userName}
        userRole={userRole}
        onLogout={onLogout}
        menuOpen={sidebar.open}
        menuId={SIDEBAR_ID}
        onToggleMenu={sidebar.toggle}
      />
      <Sidebar
        id={SIDEBAR_ID}
        items={navItems}
        activeId={activeNavId}
        onNavigate={sidebar.isNarrow ? sidebar.closeOverlay : undefined}
      />
      {sidebar.isNarrow && sidebar.open ? (
        <div className="sidebar-backdrop" onClick={sidebar.closeOverlay}></div>
      ) : null}

      <main className="content" id="main-content" role="main">
        {children}
      </main>
    </div>
  )
}
