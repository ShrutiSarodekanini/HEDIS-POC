const numberFormatter = new Intl.NumberFormat("en-US")

export function formatInteger(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return numberFormatter.format(value)
}

export function formatRate(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  const normalized = value <= 1 ? value * 100 : value
  return `${normalized.toFixed(1)}%`
}

export function toMeterWidth(value: number | null | undefined): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return 0
  }
  const normalized = value <= 1 ? value * 100 : value
  return Math.max(0, Math.min(100, normalized))
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "-"
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return "-"
  }

  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)
}
