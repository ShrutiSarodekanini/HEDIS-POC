import type { QualityCheck } from "../../models/quality"

type Details = Record<string, unknown> | null | undefined

// The API can send numeric columns as strings such as "3.0", so both forms are accepted.
const formatQualityNumber = (value: unknown): string => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value)
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? String(parsed) : value
  }
  return "-"
}

export const qualityTitleCase = (value: string | null | undefined): string => {
  const text = (value ?? "").trim()
  if (!text) {
    return "-"
  }
  return text.replace(/_/g, " ").replace(/\b\w/g, (part) => part.toUpperCase())
}

const field = (details: Details, key: string): unknown => (details ? details[key] : undefined)

export const failedCheckSentence = (check: QualityCheck): string => {
  const subject = check.measure_id ?? check.resource_type ?? "-"
  const metric = formatQualityNumber(check.metric_value)
  const threshold = formatQualityNumber(check.threshold_value)

  if (check.check_name === "measure_value_range") {
    const min = formatQualityNumber(field(check.details, "min"))
    const max = formatQualityNumber(field(check.details, "max"))
    return `${subject}: ${metric} values outside the expected range ${min} to ${max}`
  }
  if (check.check_name === "measure_clinical_evidence") {
    return `${subject}: no clinical evidence found in the data`
  }
  if (check.check_name === "measure_patient_coverage") {
    return `${subject}: ${metric} patients with evidence, needs at least ${threshold}`
  }
  return `${qualityTitleCase(check.check_name)} (${subject}): ${metric} against threshold ${threshold}`
}
