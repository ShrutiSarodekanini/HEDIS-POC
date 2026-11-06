export const CHANNEL_LABELS: Record<string, string> = {
  abstractor_review: "Abstractor review",
  member_outreach_call: "Member outreach call",
  mailed_fit_kit: "Mailed FIT kit",
  point_of_care_alert: "Point-of-care alert",
  provider_gap_list: "Provider gap list",
  chart_review: "Chart review",
}

export const toTierClass = (tier: string): string => {
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

export const formatNumber = (value: number | null | undefined, digits = 3): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return value.toFixed(digits)
}
