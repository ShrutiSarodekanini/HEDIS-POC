import { useMemo, useState } from "react"

import { GapDetailsModal } from "../components/prioritization/GapDetailsModal"
import { PrioritizedWorklist } from "../components/prioritization/PrioritizedWorklist"
import { CHANNEL_LABELS, formatNumber } from "../components/prioritization/prioritizationFormat"
import { usePrioritizationWorkflow } from "../hooks/usePrioritizationWorkflow"
import type { PrioritizationWorklistItem } from "../models/prioritization"
import { formatDateTime, formatInteger, toMeterWidth } from "../utils/format"

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

  const [detailsItem, setDetailsItem] = useState<PrioritizationWorklistItem | null>(null)

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

      <PrioritizedWorklist
        worklist={worklist}
        measures={overview?.measures ?? []}
        filters={filters}
        selectedGapId={selectedGapId}
        loadingWorklist={loadingWorklist}
        statusMessage={statusMessage}
        errorMessage={errorMessage}
        setFilter={setFilter}
        selectGap={selectGap}
        onOpenDetails={setDetailsItem}
        showAdvancedFilters
      />

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
              <article className="kpi-card prio-channel-kpi">
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

      {detailsItem ? (
        <GapDetailsModal
          item={detailsItem}
          onGapChanged={() => {
            void refreshAll()
          }}
          onClose={() => {
            setDetailsItem(null)
          }}
        />
      ) : null}
    </section>
  )
}
