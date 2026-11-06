import { useEffect, useMemo, useState } from "react"

import { useDashboardData } from "./hooks/useDashboardData"
import { useShellRoute } from "./hooks/useShellRoute"
import { ConsoleLayout } from "./layouts/ConsoleLayout"
import type { AuthSession, AuthUser } from "./models/auth"
import { AdministratorPage } from "./pages/AdministratorPage"
import { AuthAccessPage } from "./pages/AuthAccessPage"
import { DashboardPage } from "./pages/DashboardPage"
import { RunCalculationPage } from "./pages/RunCalculationPage"
import { DataQualityPage } from "./pages/DataQualityPage"
import { StarRatingPage } from "./pages/StarRatingPage"
import { PrioritizationPage } from "./pages/PrioritizationPage"
import { EvidenceReviewPage } from "./pages/EvidenceReviewPage"
import { RunIntegrationPage } from "./pages/RunIntegrationPage"
import { discoverAuthCapabilities, getCurrentUser, signOut } from "./services/auth.service"
import { clearAuthSession, loadAuthSession, updateAuthSessionUser } from "./services/authSession"
import { envConfig } from "./config/env"
import { canOpenView, isAdmin, visibleNavigationItems } from "./utils/access"
import { formatDateTime, formatInteger } from "./utils/format"

function App() {
  const routeView = useShellRoute()
  const [authReady, setAuthReady] = useState<boolean>(false)
  const [authEnabled, setAuthEnabled] = useState<boolean>(false)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const capabilities = await discoverAuthCapabilities()
        if (cancelled) {
          return
        }

        setAuthEnabled(capabilities.authEnabled)

        if (!capabilities.authEnabled) {
          setAuthReady(true)
          return
        }

        const session = loadAuthSession()
        if (!session) {
          setAuthReady(true)
          return
        }

        try {
          const user = await getCurrentUser()
          if (cancelled) {
            return
          }
          updateAuthSessionUser(user)
          setAuthUser(user)
        } catch {
          clearAuthSession()
          if (!cancelled) {
            setAuthUser(null)
          }
        }
      } catch {
        if (!cancelled) {
          setAuthEnabled(false)
        }
      } finally {
        if (!cancelled) {
          setAuthReady(true)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  const role = authUser?.role ?? envConfig.defaultUserRole
  const userIsAdmin = isAdmin(role)
  const routeAllowed = canOpenView(routeView, role)
  const activeView = routeAllowed ? routeView : "dashboard"

  const roleKnown = authReady && (!authEnabled || authUser !== null)

  useEffect(() => {
    // Wait for the signed-in role so an admin's deep link is not redirected during startup.
    if (roleKnown && !routeAllowed) {
      window.location.hash = "#view-dashboard"
    }
  }, [roleKnown, routeAllowed])

  const { snapshot, loading, reload } = useDashboardData(activeView === "dashboard")

  const dashboard = snapshot?.dashboard.data
  const runMeta = dashboard
    ? `${formatDateTime(snapshot?.fetchedAt)} | ${formatInteger(dashboard.patient_rows.length)} patients | ${formatInteger(dashboard.measure_cards.length)} measures`
    : activeView === "dashboard"
      ? (loading ? "Loading dashboard summary..." : "No dashboard snapshot loaded yet")
      : "Open Dashboard for live backend summary"

  const navItems = useMemo(() => visibleNavigationItems(role), [role])

  if (!authReady) {
    return (
      <section className="auth-shell" id="view-auth-loading">
        <article className="card state-card">
          <h2>Loading workspace</h2>
          <p className="card-message">Checking backend capabilities and authentication state...</p>
        </article>
      </section>
    )
  }

  if (authEnabled && !authUser) {
    return (
      <AuthAccessPage
        appName={envConfig.appName}
        authAvailable={authEnabled}
        onSignedIn={(session: AuthSession) => {
          setAuthUser(session.user)
        }}
      />
    )
  }

  return (
    <ConsoleLayout
      appName={envConfig.appName}
      runLabel="Last patient run"
      runMeta={runMeta}
      userName={authUser?.email ?? envConfig.defaultUserName}
      userRole={role}
      navItems={navItems}
      activeNavId={activeView}
      onLogout={() => {
        void (async () => {
          try {
            if (authEnabled) {
              await signOut()
            }
          } catch {
            // Ignore signout API errors and clear local session regardless.
          } finally {
            clearAuthSession()
            setAuthUser(null)
            window.location.hash = "#view-dashboard"
          }
        })()
      }}
    >
      {activeView === "ingestion" ? (
        <RunIntegrationPage isAdmin={userIsAdmin} />
      ) : activeView === "calculation" ? (
        <RunCalculationPage />
      ) : activeView === "quality" ? (
        <DataQualityPage />
      ) : activeView === "stars" ? (
        <StarRatingPage />
      ) : activeView === "prioritization" ? (
        <PrioritizationPage />
      ) : activeView === "evidence" ? (
        <EvidenceReviewPage />
      ) : activeView === "admin" ? (
        <AdministratorPage actorEmail={authUser?.email ?? ""} actorRole={authUser?.role ?? "user"} />
      ) : (
        <DashboardPage
          snapshot={snapshot}
          loading={loading}
          isAdmin={userIsAdmin}
          onReload={reload}
        />
      )}
    </ConsoleLayout>
  )
}

export default App
