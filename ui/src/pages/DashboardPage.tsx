import { useCallback, useState } from "react"

import { DashboardPrioritizedGaps } from "../components/dashboard/DashboardPrioritizedGaps"
import { DashboardStarRating } from "../components/dashboard/DashboardStarRating"
import { LatestBatchTile } from "../components/dashboard/LatestBatchTile"
import { QualitySummaryCard } from "../components/dashboard/QualitySummaryCard"
import { useLatestBatch, type UseLatestBatchResult } from "../hooks/useLatestBatch"
import type { DashboardSnapshot, StarDashboardMeasureCard } from "../models/dashboard"
import {
  formatDateTime,
  formatInteger,
  formatRate,
  toMeterWidth,
} from "../utils/format"

const EMPTY_TEXT = "Results appear here after an ingestion run finishes."

interface DashboardPageProps {
  snapshot: DashboardSnapshot | null
  loading: boolean
  isAdmin: boolean
  onReload: () => Promise<void>
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

export function DashboardPage({ snapshot, loading, isAdmin, onReload }: DashboardPageProps) {
  // Bumped on every step change and when the batch ends; cards reload in place when it changes.
  const [dataVersion, setDataVersion] = useState<number>(0)

  const handleBatchChanged = useCallback(() => {
    setDataVersion((previous) => previous + 1)
    void onReload()
  }, [onReload])

  const latest = useLatestBatch(handleBatchChanged, handleBatchChanged)

  return (
    <section id="view-dashboard" className="view">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>
            Results of the latest ingestion run. Every step after ingestion runs in the background and
            shows up here when it finishes.
          </p>
        </div>
      </div>

      {!snapshot ? (
        <>
          <div className="kpi-grid five">
            <LatestBatchTile latest={latest} syncedAt={null} />
          </div>
          <article className="card state-card">
            <p>{loading ? "Loading dashboard data..." : EMPTY_TEXT}</p>
          </article>
        </>
      ) : (
        <DashboardResults
          snapshot={snapshot}
          isAdmin={isAdmin}
          dataVersion={dataVersion}
          latest={latest}
          onGapChanged={handleBatchChanged}
        />
      )}
    </section>
  )
}

function DashboardResults({
  snapshot,
  isAdmin,
  dataVersion,
  latest,
  onGapChanged,
}: {
  snapshot: DashboardSnapshot
  isAdmin: boolean
  dataVersion: number
  latest: UseLatestBatchResult
  onGapChanged: () => void
}) {
  const catalog = snapshot.catalog
  const dashboard = snapshot.dashboard

  const measureCards = dashboard.data?.measure_cards ?? []
  const selectedMeasure = dashboard.data?.selected_measure
  const selectedMeasureGaps = dashboard.data?.selected_measure_gaps ?? []
  const overall = dashboard.data?.overall

  const openCareGaps = snapshot.openGapCount
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
  ]

  const measureRows = [...measureCards]
    .filter((card) => card.star_eligible)
    .sort((a, b) => (b.projected_rate ?? Number.NEGATIVE_INFINITY) - (a.projected_rate ?? Number.NEGATIVE_INFINITY))
    .slice(0, 5)

  const tierRows = buildPriorityMix(measureCards)

  return (
    <>
      <div className="kpi-grid five">
        {kpis.map((kpi) => (
          <article key={kpi.label} className={`kpi-card ${kpi.accent ?? "neutral"}`}>
            <span>{kpi.label}</span>
            <strong>{kpi.value}</strong>
            <small className="kpi-time">{kpi.note}</small>
          </article>
        ))}
        <LatestBatchTile latest={latest} syncedAt={snapshot.fetchedAt} />
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
              emptyText={EMPTY_TEXT}
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
              emptyText={EMPTY_TEXT}
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

          {isAdmin ? (
            <a className="text-link" href="#view-prioritization">
              View prioritized worklist
            </a>
          ) : null}
        </article>
      </div>

      <QualitySummaryCard dataVersion={dataVersion} latestBatch={latest.batch} />

      <h2 className="zone-title">Prioritized gaps</h2>

      <DashboardPrioritizedGaps
        dataVersion={dataVersion}
        batchRunning={latest.batch?.status === "running"}
        onGapChanged={onGapChanged}
      />

      <h2 className="zone-title">Star rating</h2>

      <DashboardStarRating dataVersion={dataVersion} fetchedAt={snapshot.fetchedAt} isAdmin={isAdmin} />

      <p className="footnote">
        Live dashboard values are read from /api/star-rating/catalog and
        /api/star-dashboard/calculate. Missing data is isolated to each card.
      </p>

      {catalog.error ? <p className="card-message error">Catalog: {catalog.error}</p> : null}
    </>
  )
}
