import type { DashboardSnapshot, StarDashboardMeasureCard } from "../models/dashboard"
import {
  formatDateTime,
  formatInteger,
  formatRate,
  toMeterWidth,
} from "../utils/format"

interface DashboardPageProps {
  snapshot: DashboardSnapshot | null
  loading: boolean
  onRetry: () => void
}

interface KpiItem {
  label: string
  value: string
  note: string
  accent?: "neutral" | "good" | "warn" | "danger"
}

interface CardStateProps {
  error: string | null
  emptyText: string
  hasData: boolean
}

function CardState({ error, emptyText, hasData }: CardStateProps) {
  if (error) {
    return <p className="card-message error">{error}</p>
  }

  if (!hasData) {
    return <p className="card-message">{emptyText}</p>
  }

  return null
}

function buildPriorityMix(cards: StarDashboardMeasureCard[]) {
  if (cards.length === 0) {
    return [
      { tier: "high", count: 0, ratio: 0 },
      { tier: "medium", count: 0, ratio: 0 },
      { tier: "low", count: 0, ratio: 0 },
    ] as const
  }

  const sorted = [...cards].sort((a, b) => b.total_gaps - a.total_gaps)
  const chunkSize = Math.max(1, Math.ceil(sorted.length / 3))
  const high = sorted.slice(0, chunkSize)
  const medium = sorted.slice(chunkSize, chunkSize * 2)
  const low = sorted.slice(chunkSize * 2)

  const sum = (rows: StarDashboardMeasureCard[]) =>
    rows.reduce((acc, row) => acc + row.total_gaps, 0)

  const highCount = sum(high)
  const mediumCount = sum(medium)
  const lowCount = sum(low)
  const total = highCount + mediumCount + lowCount

  return [
    {
      tier: "high",
      count: highCount,
      ratio: total > 0 ? (highCount / total) * 100 : 0,
    },
    {
      tier: "medium",
      count: mediumCount,
      ratio: total > 0 ? (mediumCount / total) * 100 : 0,
    },
    {
      tier: "low",
      count: lowCount,
      ratio: total > 0 ? (lowCount / total) * 100 : 0,
    },
  ] as const
}

export function DashboardPage({ snapshot, loading, onRetry }: DashboardPageProps) {
  if (loading && !snapshot) {
    return (
      <section id="view-dashboard" className="view">
        <div className="page-head">
          <div>
            <h1>Dashboard</h1>
            <p>Loading live workflow metrics from backend services.</p>
          </div>
        </div>
        <article className="card state-card">
          <p>Loading dashboard data...</p>
        </article>
      </section>
    )
  }

  if (!snapshot) {
    return (
      <section id="view-dashboard" className="view">
        <div className="page-head">
          <div>
            <h1>Dashboard</h1>
            <p>The dashboard did not receive a data snapshot from the backend.</p>
          </div>
        </div>
        <article className="card state-card error">
          <p>No data is available yet.</p>
          <button type="button" className="btn primary" onClick={onRetry}>
            Retry
          </button>
        </article>
      </section>
    )
  }

  const catalog = snapshot.catalog
  const dashboard = snapshot.dashboard

  const measureCards = dashboard.data?.measure_cards ?? []
  const selectedMeasure = dashboard.data?.selected_measure
  const selectedMeasureGaps = dashboard.data?.selected_measure_gaps ?? []
  const patientRows = dashboard.data?.patient_rows ?? []
  const overall = dashboard.data?.overall

  const openCareGaps = measureCards.reduce((sum, card) => sum + card.total_gaps, 0)
  const digitalReadiness =
    overall?.projected_star !== null && overall?.projected_star !== undefined
      ? (overall.projected_star / 5) * 100
      : null
  const openSelectedGaps = selectedMeasureGaps.filter((gap) => !gap.is_closed)

  const kpis: KpiItem[] = [
    {
      label: "Open care gaps",
      value: formatInteger(openCareGaps),
      note: dashboard.error ? "Data unavailable" : `Updated ${formatDateTime(snapshot.fetchedAt)}`,
    },
    {
      label: "Digital readiness score",
      value: formatRate(digitalReadiness),
      note: dashboard.error
        ? "Data unavailable"
        : "Projected overall star progress toward 5-star target",
    },
    {
      label: "High-priority worklist",
      value: formatInteger(openSelectedGaps.length),
      note: dashboard.error
        ? "Data unavailable"
        : `Open gaps in selected measure ${selectedMeasure?.measure_id ?? "-"}`,
      accent: "warn",
    },
    {
      label: "Evidence pending review",
      value: formatInteger(openSelectedGaps.length),
      note: dashboard.error
        ? "Data unavailable"
        : "Selected measure gaps pending closure",
      accent: "good",
    },
    {
      label: "Latest batch",
      value: dashboard.error ? "UNAVAILABLE" : "AVAILABLE",
      note: dashboard.error ? "Dashboard API failed" : `Synced ${formatDateTime(snapshot.fetchedAt)}`,
      accent: dashboard.error ? "danger" : "good",
    },
  ]

  const measureRows = [...measureCards]
    .filter((card) => card.star_eligible)
    .sort((a, b) => b.projected_rate - a.projected_rate)
    .slice(0, 5)

  const tierRows = buildPriorityMix(measureCards)

  const selectedGap = selectedMeasureGaps[0] ?? null
  const evidenceQueue = selectedMeasureGaps.slice(0, 2)
  const memberRows = patientRows.slice(0, 8)

  const currentOverallStar = overall?.baseline_star ?? null
  const projectedOverallStar = overall?.projected_star ?? null
  const expectedChange = overall?.star_delta ?? null
  const progressToFiveStar =
    projectedOverallStar !== null && projectedOverallStar !== undefined
      ? (projectedOverallStar / 5) * 100
      : null

  return (
    <section id="view-dashboard" className="view">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>
            Action-first operational view mapped to the currently available backend
            star endpoints.
          </p>
        </div>
        <button type="button" className="btn outline" onClick={onRetry}>
          Refresh
        </button>
      </div>

      <div className="kpi-grid five">
        {kpis.map((kpi) => (
          <article key={kpi.label} className={`kpi-card ${kpi.accent ?? "neutral"}`}>
            <span>{kpi.label}</span>
            <strong>{kpi.value}</strong>
            <small className="kpi-time">{kpi.note}</small>
          </article>
        ))}
      </div>

      <h2 className="zone-title">How performance is trending</h2>

      <div className="grid two">
        <article className="card dashboard-measure-card">
          <div className="card-head">
            <h2>Measure performance</h2>
            <small>Live values from star dashboard measure cards</small>
          </div>

          <div className="measure-alerts">
            <CardState
              error={dashboard.error}
              emptyText="No measure performance data is available."
              hasData={measureRows.length > 0}
            />

            {measureRows.map((measure) => (
              <article key={measure.measure_id} className="measure-alert info">
                <div className="metric-row">
                  <span>{measure.measure_name}</span>
                  <b>{formatRate(measure.projected_rate)}</b>
                </div>
                <div className="meter">
                  <span style={{ width: `${toMeterWidth(measure.projected_rate)}%` }}></span>
                </div>
              </article>
            ))}
          </div>
        </article>

        <article className="card dashboard-priority-card">
          <div className="card-head">
            <h2>Gap priority mix</h2>
            <small>Distribution based on measure gap volumes</small>
          </div>

          <div className="table-wrap priority-mix-table">
            <CardState
              error={dashboard.error}
              emptyText="Priority mix data is not available."
              hasData={measureCards.length > 0}
            />
            <table>
              <thead>
                <tr>
                  <th>Priority tier</th>
                  <th>Members</th>
                  <th>Score index</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {tierRows.map((row) => (
                  <tr key={row.tier}>
                    <td>{row.tier[0].toUpperCase() + row.tier.slice(1)}</td>
                    <td>{formatInteger(row.count)}</td>
                    <td>{formatRate(row.ratio)}</td>
                    <td>
                      <span className={`badge ${row.tier === "high" ? "good" : row.tier === "medium" ? "info" : "warn"}`}>
                        {row.tier === "high"
                          ? "High focus"
                          : row.tier === "medium"
                            ? "Balanced"
                            : "Monitor"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <a className="text-link" href="#view-prioritization">
            View prioritized worklist
          </a>
        </article>
      </div>

      <article className="card workbench">
        <div className="card-head row-split">
          <div>
            <h2>Gap workbench - human-in-the-loop closure</h2>
            <small>
              One selected measure gap, closure state, and projection selection in one
              decision surface.
            </small>
          </div>
        </div>
        <p className="freshness">
          Last updated {formatDateTime(snapshot.fetchedAt)} | Source: star dashboard
          gaps
        </p>

        <CardState
          error={dashboard.error}
          emptyText="Gap workbench data is not available."
          hasData={selectedGap !== null && selectedMeasure !== null}
        />

        {selectedGap && selectedMeasure ? (
          <div className="grid three">
            <article className="subcard">
              <h3>Gap</h3>
              <p className="title">
                {selectedGap.member_id} - {selectedMeasure.measure_name}
              </p>
              <p>Care gap id: {selectedGap.care_gap_id}</p>
              <div className="chips">
                <span className="chip warn">{selectedGap.status}</span>
                <span className="chip">Measure {selectedMeasure.measure_id}</span>
              </div>
            </article>

            <article className="subcard">
              <h3>Priority</h3>
              <p className="title">
                <span className="chip good">
                  {selectedGap.is_closed ? "Closed" : "Open"}
                </span>{" "}
                {selectedMeasure.total_gaps} measure gaps
              </p>
              <p>Selected for projection: {selectedGap.selected_for_projection ? "Yes" : "No"}</p>
              <p>Member: {selectedGap.member_name}</p>
            </article>

            <article className="subcard">
              <h3>Evidence</h3>
              <p>
                Evidence detail is not present in this endpoint. Use evidence API for
                passage-level review.
              </p>
              <p className="title">
                <span className="chip info">{selectedGap.is_closed ? "Closed" : "Proposed"}</span> backend state
              </p>
            </article>
          </div>
        ) : null}

        <p className="note">
          Confirming evidence and closing a gap require reviewer-backed decisions in
          backend workflow.
        </p>

        <div className="actions">
          <button
            type="button"
            className="btn outline"
            onClick={() => {
              window.location.hash = "#view-evidence"
            }}
          >
            Open full review
          </button>
          <button type="button" className="btn primary">
            Confirm evidence
          </button>
          <button type="button" className="btn success">
            Close gap
          </button>
        </div>
      </article>

      <article className="card star-overall-card">
        <div className="card-head row-split">
          <h2>Overall star calculation</h2>
          <a className="text-link" href="#view-stars">
            Open Star Rating
          </a>
        </div>
        <p className="freshness">
          Last updated {formatDateTime(snapshot.fetchedAt)} | Source: star dashboard
          overall
        </p>

        <CardState
          error={dashboard.error}
          emptyText="Star metrics are not available."
          hasData={overall !== null && overall !== undefined}
        />

        <div className="kpi-grid four star-kpis">
          <article className="kpi-card">
            <span>Rated measures</span>
            <strong>{formatInteger(overall?.rated_measure_count)}</strong>
          </article>
          <article className="kpi-card">
            <span>Current overall star</span>
            <strong>{currentOverallStar === null ? "-" : currentOverallStar.toFixed(2)}</strong>
          </article>
          <article className="kpi-card">
            <span>Projected overall star</span>
            <strong>{projectedOverallStar === null ? "-" : projectedOverallStar.toFixed(2)}</strong>
          </article>
          <article className="kpi-card">
            <span>Expected change</span>
            <strong className="good-text">
              {expectedChange === null ? "-" : `${expectedChange >= 0 ? "+" : ""}${expectedChange.toFixed(2)}`}
            </strong>
          </article>
        </div>

        <div className="metric-row">
          <span>Progress toward 5-star target</span>
          <b>{formatRate(progressToFiveStar)}</b>
        </div>
        <div className="meter">
          <span style={{ width: `${toMeterWidth(progressToFiveStar)}%` }}></span>
        </div>
        <p className="note">
          Projected score reflects currently selected gaps from star dashboard
          endpoint.
        </p>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Recent member scoring rows</h2>
          <small>Source: star dashboard patient rows</small>
        </div>
        <p className="freshness">Last updated {formatDateTime(snapshot.fetchedAt)}</p>

        <CardState
          error={dashboard.error}
          emptyText="Member scoring rows are not available."
          hasData={memberRows.length > 0}
        />

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Member</th>
                <th>Measure</th>
                <th>Total gaps</th>
                <th>Projected rate</th>
                <th>Projected star</th>
              </tr>
            </thead>
            <tbody>
              {memberRows.map((row) => (
                <tr key={`${row.member_id}-${row.measure_id}`}>
                  <td>{row.member_id}</td>
                  <td>{row.measure_name}</td>
                  <td>{formatInteger(row.total_gaps)}</td>
                  <td>{formatRate(row.projected_rate)}</td>
                  <td>{row.projected_star ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Evidence review queue</h2>
          <a className="text-link" href="#view-evidence">
            View all
          </a>
        </div>
        <p className="freshness">Last updated {formatDateTime(snapshot.fetchedAt)}</p>

        <CardState
          error={dashboard.error}
          emptyText="No selected measure gaps are available."
          hasData={evidenceQueue.length > 0}
        />

        {evidenceQueue.map((gap) => (
          <div key={gap.care_gap_id} className="queue-item">
            <p>
              {gap.member_id} - Care gap {gap.care_gap_id}
            </p>
            <span>{gap.is_closed ? "Closed" : "Open"}</span>
          </div>
        ))}
      </article>

      <p className="footnote">
        Live dashboard values are read from /api/star-rating/catalog and
        /api/star-dashboard/calculate. Missing data is isolated to each card.
      </p>

      {catalog.error ? <p className="card-message error">Catalog: {catalog.error}</p> : null}
    </section>
  )
}
