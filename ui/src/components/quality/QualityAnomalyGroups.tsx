import { useState } from "react"

import type { QualityAnomaly } from "../../models/quality"
import { formatDateTime, formatInteger } from "../../utils/format"
import {
  ANOMALY_TYPE_INFO,
  humanize,
  shortPatient,
  toAnomalyFacts,
  toRunStatusClass,
  toSeverityKey,
  type AnomalyGroup,
} from "./qualityDisplay"

const MAX_ANOMALIES_PER_GROUP = 20
const COMPACT_ANOMALIES_PER_GROUP = 6

function AnomalyCard({ anomaly }: { anomaly: QualityAnomaly }) {
  const facts = toAnomalyFacts(anomaly.details)
  const bits = [
    anomaly.resource_type ? `Resource: ${anomaly.resource_type}` : null,
    anomaly.patient_id ? shortPatient(anomaly.patient_id) : null,
    anomaly.detected_at ? formatDateTime(anomaly.detected_at) : null,
  ].filter((item): item is string => Boolean(item))

  return (
    <article className={`dq-card dq-card-${toSeverityKey(anomaly.severity)}`}>
      <div className="dq-card-head">
        <span className={toRunStatusClass(String(anomaly.severity ?? "unknown"))}>
          {humanize(String(anomaly.severity ?? "unknown"))}
        </span>
        {bits.length > 0 ? <span className="muted small">{bits.join(" | ")}</span> : null}
      </div>
      {facts.length > 0 ? (
        <dl className="facts">
          {facts.map((fact) => (
            <div key={`${anomaly.anomaly_id}-${fact.label}`}>
              <dt>{fact.label}</dt>
              <dd className="mono">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </article>
  )
}

interface QualityAnomalyGroupsProps {
  groups: AnomalyGroup[]
  compact: boolean
}

export function QualityAnomalyGroups({ groups, compact }: QualityAnomalyGroupsProps) {
  const [expandedTypes, setExpandedTypes] = useState<ReadonlySet<string>>(new Set())

  return (
    <>
      {groups.map((group) => {
        const typeInfo = ANOMALY_TYPE_INFO[group.type] ?? {
          title: humanize(group.type),
          description: "",
        }
        const expanded = expandedTypes.has(group.type)
        const limit = compact ? (expanded ? group.items.length : COMPACT_ANOMALIES_PER_GROUP) : MAX_ANOMALIES_PER_GROUP
        const visibleItems = group.items.slice(0, limit)
        const hiddenCount = group.items.length - visibleItems.length
        const cards = (
          <div className="dq-cards">
            {visibleItems.map((anomaly) => (
              <AnomalyCard key={anomaly.anomaly_id} anomaly={anomaly} />
            ))}
          </div>
        )

        if (compact) {
          const topSeverity = String(group.items[0]?.severity ?? "unknown")
          return (
            <details key={group.type} className="dq-group dq-group-fold">
              <summary className="dq-group-head">
                <h3>{typeInfo.title}</h3>
                <span className="status">{formatInteger(group.items.length)}</span>
                <span className={toRunStatusClass(topSeverity)}>{humanize(topSeverity)}</span>
              </summary>
              {typeInfo.description ? <p className="note small">{typeInfo.description}</p> : null}
              {cards}
              {hiddenCount > 0 ? (
                <div className="actions">
                  <button
                    type="button"
                    className="btn outline calc-small"
                    onClick={() => {
                      setExpandedTypes((current) => new Set(current).add(group.type))
                    }}
                  >
                    Show all {formatInteger(group.items.length)}
                  </button>
                </div>
              ) : null}
            </details>
          )
        }

        return (
          <section key={group.type} className="dq-group">
            <div className="dq-group-head">
              <h3>{typeInfo.title}</h3>
              <span className="status">{formatInteger(group.items.length)}</span>
            </div>
            {typeInfo.description ? <p className="note small">{typeInfo.description}</p> : null}
            {cards}
            {hiddenCount > 0 ? (
              <p className="note small">and {formatInteger(hiddenCount)} more</p>
            ) : null}
          </section>
        )
      })}
    </>
  )
}
