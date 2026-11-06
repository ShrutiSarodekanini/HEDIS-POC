import type { EvidenceGapDetail } from "../../models/evidence"
import { CHANNEL_LABELS } from "../prioritization/prioritizationFormat"
import { formatDelta, formatPercent, impactLine, titleCase } from "./evidenceFormat"

/** Sections 1 to 5 of the gap panel: the gap, likelihood, star value, reachability and priority. */
export function GapFacts({ detail }: { detail: EvidenceGapDetail }) {
  const breakdown = detail.breakdown
  const likelihood = breakdown?.likelihood
  const value = breakdown?.value
  const engagement = breakdown?.engagement

  return (
    <>
      <section className="psec">
        <h3>1 · Gap</h3>
        <dl className="facts">
          <div><dt>Measure</dt><dd title={`${detail.measureName} (${detail.measureId})`}>{detail.measureName} ({detail.measureId})</dd></div>
          <div><dt>Patient</dt><dd>{detail.patientId}</dd></div>
          <div><dt>Missing</dt><dd title={detail.missingElement || undefined}>{detail.missingElement || "-"}</dd></div>
          <div><dt>Tier</dt><dd>{titleCase(detail.tier)}</dd></div>
          <div><dt>Deadline</dt><dd>{detail.deadlineDate ?? "-"}</dd></div>
          <div><dt>Review status</dt><dd>{titleCase(detail.reviewStatus)}</dd></div>
          <div>
            <dt>Last event</dt>
            <dd>
              {detail.lastRelevantEventDate
                ? `${detail.lastRelevantEventDate} ${detail.lastRelevantEventDescription ?? ""}`
                : "none recorded"}
            </dd>
          </div>
        </dl>
      </section>

      {likelihood ? (
        <section className="psec">
          <h3>2 · How likely it closes</h3>
          <div className="calc">
            {likelihood.steps.map((step, index) => (
              <div key={`${step.label}-${index}`} className="calc-row">
                <span>{step.label}</span>
                <span className="v">{formatDelta(step.change, 4)}</span>
              </div>
            ))}
            <div className="calc-row calc-total">
              <span>Closure likelihood</span>
              <span className="v">{formatDelta(likelihood.result, 4)}</span>
            </div>
          </div>
          {likelihood.raw != null && likelihood.result != null && likelihood.raw !== likelihood.result ? (
            <p className="note small">Raw {formatDelta(likelihood.raw, 4)}, kept between {likelihood.min ?? "-"} and {likelihood.max ?? "-"}.</p>
          ) : null}
        </section>
      ) : null}

      {value ? (
        <section className="psec">
          <h3>3 · How much it helps the stars</h3>
          <dl className="facts">
            <div><dt>Done / eligible</dt><dd>{value.numerator ?? "-"} / {value.denominator ?? "-"}</dd></div>
            <div><dt>Rate</dt><dd>{formatPercent(value.rate)} to {value.rounded ?? "-"}%</dd></div>
            <div><dt>Stars now</dt><dd>{value.stars ?? "-"}</dd></div>
            <div><dt>Next star</dt><dd>{value.nextCut != null ? `${value.nextCut}%` : "already at top"}</dd></div>
            <div><dt>Measure weight</dt><dd>{value.weight ?? "-"}</dd></div>
          </dl>
          <p className="formula">
            {value.reason ? `${value.reason} -> result ${formatDelta(value.result, 4)}` : `Result ${formatDelta(value.result, 4)}`}
          </p>
        </section>
      ) : null}

      {engagement ? (
        <section className="psec">
          <h3>4 · How reachable</h3>
          <div className="calc">
            {engagement.steps.map((step, index) => (
              <div key={`${step.label}-${index}`} className="calc-row">
                <span>{step.label}</span>
                <span className="v">{formatDelta(step.change, 3)}</span>
              </div>
            ))}
            <div className="calc-row calc-total">
              <span>Engagement (max {engagement.cap ?? "-"})</span>
              <span className="v">{formatDelta(engagement.result, 3)}</span>
            </div>
          </div>
        </section>
      ) : null}

      <section className="psec">
        <h3>5 · Priority</h3>
        <p className="formula">Composite {formatDelta(breakdown?.composite, 4)}</p>
        <p className="note">Recommended channel: {CHANNEL_LABELS[breakdown?.channel ?? ""] ?? titleCase(breakdown?.channel) ?? "-"}</p>
        <p className="note">{impactLine(detail.evidence[0] ?? {
          proposalId: "",
          gapId: detail.gapId,
          gapKey: detail.key,
          patientId: detail.patientId,
          measureId: detail.measureId,
          measureName: detail.measureName,
          tier: detail.tier,
          missingElement: detail.missingElement,
          deadlineDate: detail.deadlineDate,
          documentId: "",
          pageNo: null,
          evidenceKind: "",
          evidenceType: "",
          serviceDateRaw: null,
          serviceDateFrom: null,
          serviceDateTo: null,
          value: null,
          excerpt: "",
          proposedFinding: "",
          confidence: null,
          model: null,
          reason: null,
          status: "proposed",
          reviewStatus: detail.reviewStatus,
          decisionReason: null,
          context: null,
          ruleCheck: null,
          attempt: null,
          impact: detail.impact,
        })}</p>
      </section>
    </>
  )
}
