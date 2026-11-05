export const toStatusClass = (status: string): string => {
  const normalized = status.trim().toLowerCase()
  if (normalized === "assigned" || normalized === "completed") {
    return "status status-ok"
  }
  if (normalized === "unavailable" || normalized === "running") {
    return "status status-warn"
  }
  return "status"
}

export const sortByMeasureId = <T extends { measureId: string }>(rows: T[]): T[] =>
  [...rows].sort((left, right) => left.measureId.localeCompare(right.measureId))
