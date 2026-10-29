import { navigationItems } from "./constants/navigation"
import { useDashboardData } from "./hooks/useDashboardData"
import { useShellRoute } from "./hooks/useShellRoute"
import { ConsoleLayout } from "./layouts/ConsoleLayout"
import { DashboardPage } from "./pages/DashboardPage"
import { RunCalculationPage } from "./pages/RunCalculationPage"
import { DataQualityPage } from "./pages/DataQualityPage"
import { StarRatingPage } from "./pages/StarRatingPage"
import { PrioritizationPage } from "./pages/PrioritizationPage"
import { EvidenceReviewPage } from "./pages/EvidenceReviewPage"
import { RunIntegrationPage } from "./pages/RunIntegrationPage"
import { envConfig } from "./config/env"
import { formatDateTime, formatInteger } from "./utils/format"

function App() {
  const activeView = useShellRoute()
  const { snapshot, loading, reload } = useDashboardData(activeView === "dashboard")

  const dashboard = snapshot?.dashboard.data
  const runMeta = dashboard
    ? `${formatDateTime(snapshot?.fetchedAt)} | ${formatInteger(dashboard.patient_rows.length)} patients | ${formatInteger(dashboard.measure_cards.length)} measures`
    : "Data unavailable from backend"

  return (
    <ConsoleLayout
      appName={envConfig.appName}
      runLabel="Last patient run"
      runMeta={runMeta}
      userName={envConfig.defaultUserName}
      userRole={envConfig.defaultUserRole}
      navItems={navigationItems}
      activeNavId={activeView}
    >
      {activeView === "ingestion" ? (
        <RunIntegrationPage />
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
      ) : (
        <DashboardPage
          snapshot={snapshot}
          loading={loading}
          onRetry={() => {
            void reload()
          }}
        />
      )}
    </ConsoleLayout>
  )
}

export default App
