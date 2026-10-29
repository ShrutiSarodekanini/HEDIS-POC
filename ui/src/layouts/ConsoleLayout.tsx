import type { ReactNode } from "react"

import type { NavigationItem } from "../constants/navigation"
import { Sidebar } from "../components/layout/Sidebar"
import { TopBar } from "../components/layout/TopBar"

interface ConsoleLayoutProps {
  children: ReactNode
  appName: string
  runLabel: string
  runMeta: string
  userName: string
  userRole: string
  navItems: NavigationItem[]
  activeNavId: string
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
}: ConsoleLayoutProps) {
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <TopBar
        appName={appName}
        runLabel={runLabel}
        runMeta={runMeta}
        userName={userName}
        userRole={userRole}
      />
      <Sidebar items={navItems} activeId={activeNavId} />

      <main className="content" id="main-content" role="main">
        {children}
      </main>
    </>
  )
}
