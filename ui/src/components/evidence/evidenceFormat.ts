import type { EvidenceQueueItem } from "../../models/evidence"

export const formatConfidence = (value: number | null): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "- confidence"
  }
  const normalized = value <= 1 ? value * 100 : value
  return `${normalized.toFixed(1)}% confidence`
}

export const formatDelta = (value: number | null | undefined, fractionDigits = 3): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  const prefix = value > 0 ? "+" : ""
  return `${prefix}${value.toFixed(fractionDigits)}`
}

export const formatPercent = (value: number | null | undefined, fractionDigits = 2): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return `${value.toFixed(fractionDigits)}%`
}

export const titleCase = (value: string | null | undefined): string => {
  const text = (value ?? "").trim()
  if (!text) {
    return "-"
  }
  return text
    .replaceAll("_", " ")
    .split(" ")
    .filter((part) => part.length > 0)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ")
}

export const typeName = (value: string | null | undefined): string => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (!normalized) {
    return "Evidence"
  }
  if (normalized === "bp_control") {
    return "Blood pressure control"
  }
  if (normalized === "hba1c") {
    return "HbA1c"
  }
  return titleCase(normalized)
}

export const findingSentence = (item: EvidenceQueueItem): string => {
  const parts = [
    item.evidenceKind ? titleCase(item.evidenceKind) : "Evidence",
    item.evidenceType ? typeName(item.evidenceType) : "",
    item.serviceDateRaw ? `on ${item.serviceDateRaw}` : "",
    item.value ? `value ${item.value}` : "",
  ].filter((part) => part.length > 0)
  return parts.join(" ")
}

export const impactLine = (item: EvidenceQueueItem): string => {
  if (!item.impact) {
    return "Impact preview unavailable for this gap."
  }
  if (!item.impact.available) {
    return item.impact.note ?? "Impact preview unavailable for this gap."
  }

  const closures = item.impact.closuresForNextStar == null ? "-" : String(item.impact.closuresForNextStar)
  return `Rate ${formatPercent(item.impact.rateNow)} to ${formatPercent(item.impact.rateAfter)} (${formatDelta(item.impact.changePoints, 2)} pts). Stars ${item.impact.starsNow ?? "-"} to ${item.impact.starsAfter ?? "-"}. Closures needed for next star after this gap: ${closures}.`
}

export const quoteBlock = (item: EvidenceQueueItem): string => {
  if (item.context?.text) {
    const start = Math.max(0, item.context.quoteStart)
    const end = Math.max(start, item.context.quoteEnd)
    const text = item.context.text
    const before = text.slice(0, start)
    const quote = text.slice(start, end)
    const after = text.slice(end)
    return `${before}[QUOTE]${quote}[/QUOTE]${after}`
  }
  return item.excerpt
}
