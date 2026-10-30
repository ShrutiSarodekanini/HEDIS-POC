import { useEffect, useMemo, useState } from "react"

import { usePrioritizationWorkflow } from "../hooks/usePrioritizationWorkflow"
import type { PrioritizationWorklistItem } from "../models/prioritization"
import { formatDateTime, formatInteger, toMeterWidth } from "../utils/format"

const CHANNEL_LABELS: Record<string, string> = {
  abstractor_review: "Abstractor review",
  member_outreach_call: "Member outreach call",
  mailed_fit_kit: "Mailed FIT kit",
  point_of_care_alert: "Point-of-care alert",
  provider_gap_list: "Provider gap list",
  chart_review: "Chart review",
}

const ROWS_PER_PAGE = 10

const toTierClass = (tier: string): string => {
  const normalized = tier.trim().toLowerCase()
  if (normalized === "high") {
    return "chip warn"
  }
  if (normalized === "medium") {
    return "chip info"
  }
  if (normalized === "low") {
    return "chip good"
  }
  return "chip"
}

const formatNumber = (value: number | null | undefined, digits = 3): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return value.toFixed(digits)
}

const avg = (rows: PrioritizationWorklistItem[], key: "likelihood" | "value" | "engagement"): number | null => {
  const values = rows
    .map((row) => row[key])
    .filter((value): value is number => typeof value === "number" && !Number.isNaN(value))

  if (!values.length) {
    return null
  }
  const sum = values.reduce((accumulator, value) => accumulator + value, 0)
  return sum / values.length
}

export function PrioritizationPage() {
  const {
    capabilities,
    overview,
    worklist,
    gapDetail,
    filters,
    selectedGapId,
    channelMix,
    loadingSetup,
    loadingOverview,
    loadingWorklist,
    loadingGapDetail,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup,
    refreshAll,
    setFilter,
    selectGap,
  } = usePrioritizationWorkflow()

  const [currentPage, setCurrentPage] = useState<number>(1)

  const measureOptions = useMemo(
    () => [...(overview?.measures ?? [])].sort((left, right) => left.measureId.localeCompare(right.measureId)),
    [overview?.measures],
  )

  const channelRows = useMemo(() => {
    const entries = Object.entries(channelMix)
    const total = entries.reduce((accumulator, [, count]) => accumulator + count, 0)
    return entries
      .sort((left, right) => right[1] - left[1])
      .map(([channel, count]) => ({
        channel,
        count,
        share: total > 0 ? count / total : 0,
      }))
  }, [channelMix])

  const factorAverages = useMemo(
    () => ({
      likelihood: avg(worklist, "likelihood"),
      value: avg(worklist, "value"),
      engagement: avg(worklist, "engagement"),
    }),
    [worklist],
  )

  const totalPages = Math.max(1, Math.ceil(worklist.length / ROWS_PER_PAGE))

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages)
    }
  }, [currentPage, totalPages])

  useEffect(() => {
    setCurrentPage(1)
  }, [filters.role, filters.tier, filters.measure, filters.limit])

  const pageStart = (currentPage - 1) * ROWS_PER_PAGE
  const pageEnd = pageStart + ROWS_PER_PAGE
  const visibleRows = useMemo(
    () => worklist.slice(pageStart, pageEnd),
    [worklist, pageStart, pageEnd],
  )

  if (loadingSetup) {
    return (
      <section id="view-prioritization" className="view">
        <article className="card state-card">
          <h2>Prioritization</h2>
          <p className="card-message">Loading prioritization capabilities from backend...</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-prioritization" className="view">
      <div className="page-head">
        <div>
          <h1>Prioritization</h1>
          <p>Rank open gaps using live likelihood, value, and engagement scores from backend rules.</p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={loadingOverview || loadingWorklist}
            onClick={() => {
              void refreshAll()
            }}
          >
            {loadingOverview || loadingWorklist ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Prioritization API unavailable:</strong> {setupError}
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check prioritization endpoints
            </button>
          </div>
        </article>
      ) : null}

      <article className="card">
        <div className="card-head row-split">
          <h2>Scoring snapshot</h2>
          <span className="note">
            Rules {overview?.rulesVersion ?? "-"}
            {overview?.cmsRatingYear ? ` | CMS ${overview.cmsRatingYear}` : ""}
          </span>
        </div>

        {capabilities?.mode === "dashboard" ? (
          <p className="note">
            This backend profile provides prioritization via star dashboard snapshots. Detailed likelihood/value/engagement breakdown
            fields are available only in integration profile.
          </p>
        ) : null}

        <p className="note">
          {formatInteger(worklist.length)} open gaps scored
          {overview?.lastScoredAt ? ` | Last scored ${formatDateTime(overview.lastScoredAt)}` : ""}
        </p>

        <div className="kpi-grid four">
          <article className="kpi-card">
            <span>High tier</span>
            <strong>{formatInteger(overview?.tiers.high ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Medium tier</span>
            <strong>{formatInteger(overview?.tiers.medium ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Low tier</span>
            <strong>{formatInteger(overview?.tiers.low ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Evidence pending export</span>
            <strong>
              {overview?.evidence.supplementalUnexported == null
                ? "-"
                : formatInteger(overview.evidence.supplementalUnexported)}
            </strong>
          </article>
        </div>

        <div className="prio-factors">
          <div className="prio-factor-row">
            <span>Closure likelihood</span>
            <div className="meter"><span style={{ width: `${toMeterWidth(factorAverages.likelihood)}%` }}></span></div>
            <b>{formatNumber(factorAverages.likelihood)}</b>
          </div>
          <div className="prio-factor-row">
            <span>Financial value</span>
            <div className="meter"><span style={{ width: `${toMeterWidth(factorAverages.value)}%` }}></span></div>
            <b>{formatNumber(factorAverages.value)}</b>
          </div>
          <div className="prio-factor-row">
            <span>Engagement</span>
            <div className="meter"><span style={{ width: `${toMeterWidth(factorAverages.engagement)}%` }}></span></div>
            <b>{formatNumber(factorAverages.engagement)}</b>
          </div>
        </div>

        <div className="prio-channel-mix">
          {channelRows.length > 0 ? channelRows.map((row) => (
            <div key={row.channel} className="prio-channel-row">
              <span>{CHANNEL_LABELS[row.channel] ?? row.channel}</span>
              <div className="meter"><span style={{ width: `${Math.round(row.share * 100)}%` }}></span></div>
              <b>{Math.round(row.share * 100)}%</b>
            </div>
          )) : <p className="empty">No channel mix yet.</p>}
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Filters</h2>
          <span className="note">Use backend-supported filters only</span>
        </div>

        <div className="calc-toolbar quality-filters">
          <label className="calc-check">
            <span>Role</span>
            <select
              className="text-input"
              value={filters.role}
              onChange={(event) => {
                setFilter("role", event.target.value as "" | "reviewer" | "outreach" | "doctor")
              }}
            >
              <option value="">All roles</option>
              <option value="reviewer">Reviewer</option>
              <option value="outreach">Outreach</option>
              <option value="doctor">Doctor</option>
            </select>
          </label>

          <label className="calc-check">
            <span>Tier</span>
            <select
              className="text-input"
              value={filters.tier}
              onChange={(event) => {
                setFilter("tier", event.target.value as "" | "high" | "medium" | "low")
              }}
            >
              <option value="">All tiers</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>

          <label className="calc-check">
            <span>Measure</span>
            <select
              className="text-input"
              value={filters.measure}
              onChange={(event) => {
                setFilter("measure", event.target.value)
              }}
            >
              <option value="">All measures</option>
              {measureOptions.map((measure) => (
                <option key={measure.measureId} value={measure.measureId}>
                  {measure.measureId} - {measure.measureName}
                </option>
              ))}
            </select>
          </label>

          <label className="calc-check">
            <span>Limit</span>
            <select
              className="text-input"
              value={String(filters.limit)}
              onChange={(event) => {
                setFilter("limit", Number(event.target.value))
              }}
            >
              <option value="100">100</option>
              <option value="200">200</option>
              <option value="400">400</option>
            </select>
          </label>
        </div>

        {statusMessage ? <p className="card-message">{statusMessage}</p> : null}
        {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Prioritized worklist</h2>
          <span className="note">Click a row to inspect full scoring breakdown | Page {currentPage} of {totalPages}</span>
        </div>

        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={currentPage <= 1}
            onClick={() => {
              setCurrentPage((previous) => Math.max(1, previous - 1))
            }}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn outline"
            disabled={currentPage >= totalPages}
            onClick={() => {
              setCurrentPage((previous) => Math.min(totalPages, previous + 1))
            }}
          >
            Next
          </button>
          <span className="note">
            Showing {worklist.length === 0 ? 0 : pageStart + 1}-{Math.min(pageEnd, worklist.length)} of {worklist.length}
          </span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Patient</th>
                <th>Measure</th>
                <th>Missing</th>
                <th>Tier</th>
                <th>Composite</th>
                <th>Channel</th>
                <th>Next visit</th>
              </tr>
            </thead>
            <tbody>
              {loadingWorklist ? (
                <tr>
                  <td colSpan={7} className="empty">Loading worklist...</td>
                </tr>
              ) : visibleRows.length > 0 ? (
                visibleRows.map((item) => (
                  <tr
                    key={item.gapId}
                    className={item.gapId === selectedGapId ? "history-row-active" : ""}
                    onClick={() => {
                      selectGap(item.gapId)
                    }}
                  >
                    <td>{item.patientId}</td>
                    <td>
                      <div>{item.measureName}</div>
                      <div className="note">{item.measureId}</div>
                    </td>
                    <td>{item.missingElement ?? "-"}</td>
                    <td><span className={toTierClass(item.tier)}>{item.tier}</span></td>
                    <td>{formatNumber(item.composite, 4)}</td>
                    <td>{CHANNEL_LABELS[item.channel ?? ""] ?? item.channel ?? "-"}</td>
                    <td>{item.nextVisit ? formatDateTime(item.nextVisit) : "-"}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="empty">No open gaps found for selected filters.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Gap score breakdown</h2>
          <span className="note">{gapDetail?.gap.gapId ?? "Select a gap"}</span>
        </div>

        {loadingGapDetail ? (
          <p className="card-message">Loading gap detail...</p>
        ) : gapDetail ? (
          <>
            <div className="kpi-grid four">
              <article className="kpi-card">
                <span>Tier</span>
                <strong>{gapDetail.gap.tier ?? "-"}</strong>
              </article>
              <article className="kpi-card">
                <span>Composite</span>
                <strong>{formatNumber(gapDetail.gap.compositeScore, 4)}</strong>
              </article>
              <article className="kpi-card">
                <span>Pending findings</span>
                <strong>{formatInteger(gapDetail.gap.pendingCount)}</strong>
              </article>
              <article className="kpi-card">
                <span>Recommended channel</span>
                <strong>{CHANNEL_LABELS[gapDetail.gap.recommendedChannel ?? ""] ?? gapDetail.gap.recommendedChannel ?? "-"}</strong>
              </article>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Value</th>
                    <th>Field</th>
                    <th>Value</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Patient</td>
                    <td>{gapDetail.gap.patientId}</td>
                    <td>Measure</td>
                    <td>{gapDetail.gap.measureName} ({gapDetail.gap.measureId})</td>
                  </tr>
                  <tr>
                    <td>Missing</td>
                    <td>{gapDetail.gap.missingElement ?? "-"}</td>
                    <td>Review status</td>
                    <td>{gapDetail.gap.reviewStatus}</td>
                  </tr>
                  <tr>
                    <td>Deadline</td>
                    <td>{gapDetail.gap.deadlineDate ? formatDateTime(gapDetail.gap.deadlineDate) : "-"}</td>
                    <td>Last event</td>
                    <td>
                      {gapDetail.gap.lastRelevantEventDate
                        ? `${formatDateTime(gapDetail.gap.lastRelevantEventDate)} ${gapDetail.gap.lastRelevantEventDescription ?? ""}`
                        : "-"}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {gapDetail.breakdown ? (
              <div className="prio-breakdown-grid">
                <article className="subcard">
                  <h3>Closure likelihood</h3>
                  <p className="note">Result: {formatNumber(gapDetail.breakdown.likelihood.result, 4)}</p>
                  <p className="note">Raw {formatNumber(gapDetail.breakdown.likelihood.raw, 4)} (bounded {formatNumber(gapDetail.breakdown.likelihood.min, 2)} to {formatNumber(gapDetail.breakdown.likelihood.max, 2)})</p>
                </article>

                <article className="subcard">
                  <h3>Financial value</h3>
                  <p className="note">Result: {formatNumber(gapDetail.breakdown.value.result, 4)}</p>
                  <p className="note">{gapDetail.breakdown.value.reason}</p>
                  <p className="note">
                    Rate {formatNumber(gapDetail.breakdown.value.rate, 2)}% (rounded {gapDetail.breakdown.value.rounded ?? "-"}%)
                    {gapDetail.breakdown.value.needed != null ? ` | Need ${gapDetail.breakdown.value.needed} more for next star` : ""}
                  </p>
                </article>

                <article className="subcard">
                  <h3>Engagement</h3>
                  <p className="note">Result: {formatNumber(gapDetail.breakdown.engagement.result, 4)}</p>
                  <p className="note">Cap: {formatNumber(gapDetail.breakdown.engagement.cap, 2)}</p>
                </article>

                <article className="subcard">
                  <h3>Composite formula</h3>
                  <p className="note">Composite = likelihood x value</p>
                  <p className="title">{formatNumber(gapDetail.breakdown.likelihood.result, 4)} x {formatNumber(gapDetail.breakdown.value.result, 4)} = {formatNumber(gapDetail.breakdown.composite, 4)}</p>
                  <p className="note">Channel: {CHANNEL_LABELS[gapDetail.breakdown.channel] ?? gapDetail.breakdown.channel}</p>
                </article>
              </div>
            ) : (
              <p className="card-message">Detailed breakdown is not available for this gap.</p>
            )}

            <div className="prio-impact">
              <h3>Star impact</h3>
              {gapDetail.impact ? (
                gapDetail.impact.available ? (
                  <p className="note">
                    If confirmed: rate {formatNumber(gapDetail.impact.rateNow, 2)}% to {formatNumber(gapDetail.impact.rateAfter, 2)}%
                    ({gapDetail.impact.changePoints == null ? "-" : `${gapDetail.impact.changePoints >= 0 ? "+" : ""}${formatNumber(gapDetail.impact.changePoints, 2)}`} pts),
                    stars {gapDetail.impact.starsNow ?? "-"} to {gapDetail.impact.starsAfter ?? "-"}
                    {gapDetail.impact.closuresForNextStar != null ? ` | ${gapDetail.impact.closuresForNextStar} more closures for next star` : ""}
                  </p>
                ) : (
                  <p className="note">{gapDetail.impact.note ?? "Impact is not available for this gap."}</p>
                )
              ) : (
                <p className="note">Impact data is not available.</p>
              )}
            </div>

            <div className="prio-evidence-meta">
              <span>Documents: {formatInteger(gapDetail.documentsCount)}</span>
              <span>Proposed: {formatInteger(gapDetail.evidenceSummary.proposed)}</span>
              <span>Confirmed: {formatInteger(gapDetail.evidenceSummary.confirmed)}</span>
              <span>Rejected: {formatInteger(gapDetail.evidenceSummary.rejected)}</span>
              <span>AI runs: {formatInteger(gapDetail.aiRunsCount)}</span>
            </div>
          </>
        ) : (
          <p className="card-message">Select a gap from the worklist to inspect details.</p>
        )}
      </article>
    </section>
  )
}
