import type { QualityAnomaly } from "../../models/quality"

export const SEVERITY_ORDER = ["critical", "high", "medium", "low"]

export const ANOMALY_TYPE_INFO: Record<string, { title: string; description: string }> = {
  measure_evidence_missing: {
    title: "Measure evidence missing",
    description: "No records with the required codes were found for this measure.",
  },
  measure_low_patient_coverage: {
    title: "Low patient coverage for a measure",
    description: "Too few patients have evidence for this measure to trust its results.",
  },
  measure_observation_missing: {
    title: "Measure observations missing",
    description: "Observation codes required by this measure are absent from the batch.",
  },
  mapping_error: {
    title: "Mapping error",
    description: "An observation unit or value did not map as expected.",
  },
  mapping_error_rate: {
    title: "Mapping error rate above threshold",
    description: "Too many records failed validation during ingestion.",
  },
  coverage_gap_violation: {
    title: "Coverage gap over threshold",
    description: "Some members have enrollment gaps above the allowed limit.",
  },
  required_feed_missing: {
    title: "Required feed missing",
    description: "No records arrived for a resource type required by measures.",
  },
  feed_dropout: {
    title: "Feed dropped out",
    description: "A feed that normally has data sent none in this batch.",
  },
  volume_drop: {
    title: "Feed volume dropped",
    description: "This feed sent significantly fewer records than baseline.",
  },
  volume_spike: {
    title: "Feed volume spiked",
    description: "This feed sent significantly more records than baseline.",
  },
}

const FACT_LABELS: Record<string, string> = {
  measure_id: "Measure",
  code: "Code",
  reason: "Reason",
  resource_type: "Resource type",
  clinical_evidence_mode: "Evidence needed",
  max_patient_count: "Patients with evidence",
  min_distinct_patients_with_evidence: "Minimum patients needed",
  error_count: "Errors",
  warning_count: "Warnings",
  total_resources: "Total records",
  error_rate: "Error rate",
  violation_count: "Members over limit",
  threshold_gap_days: "Gap limit (days)",
  record_count: "Records",
  current_count: "Records now",
  baseline_avg_count: "Usual records",
  drop_pct: "Drop (%)",
  spike_pct: "Spike (%)",
  drop_threshold_pct: "Drop limit (%)",
  spike_threshold_pct: "Spike limit (%)",
  min_expected: "Lowest expected",
  max_expected: "Highest expected",
  actual_value: "Actual value",
  expected_unit: "Expected unit",
  actual_unit: "Actual unit",
  observation_codes: "Expected codes",
}

const REASON_LABELS: Record<string, string> = {
  missing_or_invalid_unit: "Unit missing or not expected",
  value_out_of_expected_range: "Value outside expected range",
}

const CODE_KEYS = new Set([
  "measure_id",
  "reason",
  "resource_type",
  "code",
  "expected_unit",
  "actual_unit",
  "clinical_evidence_mode",
])

const HIDDEN_KEYS = new Set(["patient_id", "observation_id", "check"])
const CODE_VALUE = /^[A-Za-z0-9_.:%/\[\]{}-]{1,40}$/

export const humanize = (value: string): string => {
  const text = String(value ?? "")
  if (!text) {
    return "-"
  }
  const replaced = text.replaceAll("_", " ")
  return replaced.charAt(0).toUpperCase() + replaced.slice(1)
}

export const shortPatient = (value: string | null | undefined): string => {
  if (!value) {
    return "pt-?"
  }
  return `pt-${String(value).slice(0, 4)}...`
}

export const formatMetricValue = (value: number | null | undefined): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return value.toFixed(2)
}

export const toRunStatusClass = (value: string): string => {
  const normalized = value.trim().toLowerCase()
  if (normalized === "completed" || normalized === "passed") {
    return "status status-ok"
  }
  if (normalized === "failed") {
    return "status status-bad"
  }
  if (normalized === "running" || normalized === "not_available") {
    return "status status-warn"
  }
  return "status"
}

export const toSeverityKey = (value: string | null | undefined): string => {
  const normalized = String(value ?? "").trim().toLowerCase()
  if (SEVERITY_ORDER.includes(normalized)) {
    return normalized
  }
  return "other"
}

const toSeverityRank = (value: string | null | undefined): number => {
  const normalized = toSeverityKey(value)
  const index = SEVERITY_ORDER.indexOf(normalized)
  return index >= 0 ? index : SEVERITY_ORDER.length
}

export const toAnomalyFacts = (
  details: Record<string, unknown> | null | undefined,
): Array<{ label: string; value: string }> => {
  if (!details || typeof details !== "object") {
    return []
  }

  const facts: Array<{ label: string; value: string }> = []
  for (const [key, raw] of Object.entries(details)) {
    if (HIDDEN_KEYS.has(key) || raw == null) {
      continue
    }

    let shown: string | null = null
    if (typeof raw === "number") {
      shown = Number.isFinite(raw) ? String(Math.round(raw * 100) / 100) : null
    } else if (typeof raw === "boolean") {
      shown = raw ? "Yes" : "No"
    } else if (typeof raw === "string" && CODE_KEYS.has(key) && CODE_VALUE.test(raw)) {
      shown = key === "reason" ? (REASON_LABELS[raw] ?? humanize(raw)) : raw
    } else if (key === "observation_codes" && Array.isArray(raw) && raw.length > 0) {
      const safeCodes = raw.filter((item): item is string => typeof item === "string" && CODE_VALUE.test(item))
      if (safeCodes.length > 0) {
        const visible = safeCodes.slice(0, 6)
        shown = visible.join(", ") + (safeCodes.length > visible.length ? ` and ${safeCodes.length - visible.length} more` : "")
      }
    }

    if (shown != null) {
      facts.push({ label: FACT_LABELS[key] ?? humanize(key), value: shown })
    }
  }

  const evidenceChecks = details.evidence_checks
  if (Array.isArray(evidenceChecks)) {
    const missingSources = evidenceChecks
      .filter((entry) => {
        if (!entry || typeof entry !== "object") {
          return false
        }
        const typed = entry as Record<string, unknown>
        return typed.row_count === 0 && typeof typed.resource_type === "string" && CODE_VALUE.test(typed.resource_type)
      })
      .map((entry) => (entry as Record<string, unknown>).resource_type as string)

    facts.push({ label: "Missing sources", value: missingSources.length > 0 ? missingSources.join(", ") : "None" })
  }

  return facts
}

export interface AnomalyGroup {
  type: string
  items: QualityAnomaly[]
}

export const groupAnomalies = (anomalies: QualityAnomaly[]): AnomalyGroup[] => {
  const grouped = new Map<string, QualityAnomaly[]>()
  for (const anomaly of anomalies) {
    const type = anomaly.check_type ?? anomaly.anomaly_type ?? "unknown"
    const current = grouped.get(type)
    if (current) {
      current.push(anomaly)
    } else {
      grouped.set(type, [anomaly])
    }
  }

  return Array.from(grouped.entries())
    .map(([type, items]) => ({
      type,
      items: [...items].sort((left, right) => toSeverityRank(left.severity) - toSeverityRank(right.severity)),
    }))
    .sort((left, right) => {
      const leftRank = toSeverityRank(left.items[0]?.severity)
      const rightRank = toSeverityRank(right.items[0]?.severity)
      if (leftRank !== rightRank) {
        return leftRank - rightRank
      }
      return right.items.length - left.items.length
    })
}

interface SeverityRow {
  key: string
  label: string
  count: number
  ratio: number
}

export const severityRows = (anomalies: QualityAnomaly[]): SeverityRow[] => {
  const counts: Record<string, number> = {}
  for (const anomaly of anomalies) {
    const key = toSeverityKey(anomaly.severity)
    counts[key] = (counts[key] ?? 0) + 1
  }

  const keys = [
    ...SEVERITY_ORDER.filter((key) => counts[key]),
    ...Object.keys(counts).filter((key) => !SEVERITY_ORDER.includes(key)).sort(),
  ]

  const total = anomalies.length
  return keys.map((key) => ({
    key,
    label: humanize(key),
    count: counts[key],
    ratio: total > 0 ? counts[key] / total : 0,
  }))
}
