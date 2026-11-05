import { useMemo } from "react"

import type { PrioritizationMeasureSummary, PrioritizationWorklistFilters } from "../../models/prioritization"

type Filters = PrioritizationWorklistFilters
type SetFilter = <K extends keyof Filters>(key: K, value: Filters[K]) => void

const TIER_OPTIONS: Array<{ value: Filters["tier"]; label: string }> = [
  { value: "", label: "All" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
]

interface WorklistToolbarProps {
  measures: PrioritizationMeasureSummary[]
  filters: Filters
  setFilter: SetFilter
  showAdvancedFilters: boolean
}

export function WorklistToolbar({ measures, filters, setFilter, showAdvancedFilters }: WorklistToolbarProps) {
  const measureOptions = useMemo(
    () => [...measures].sort((left, right) => left.measureId.localeCompare(right.measureId)),
    [measures],
  )

  const hasFilter = Boolean(filters.measure || filters.tier || filters.role)

  return (
    <div className="worklist-toolbar">
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

      <div className="calc-check">
        <span>Tier</span>
        <div className="segmented" role="group" aria-label="Tier">
          {TIER_OPTIONS.map((option) => (
            <button
              key={option.label}
              type="button"
              aria-pressed={filters.tier === option.value}
              onClick={() => {
                setFilter("tier", option.value)
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {showAdvancedFilters ? (
        <>
          <label className="calc-check">
            <span>Role</span>
            <select
              className="text-input"
              value={filters.role}
              onChange={(event) => {
                setFilter("role", event.target.value as Filters["role"])
              }}
            >
              <option value="">All roles</option>
              <option value="reviewer">Reviewer</option>
              <option value="outreach">Outreach</option>
              <option value="doctor">Doctor</option>
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
        </>
      ) : null}

      {hasFilter ? (
        <button
          type="button"
          className="btn link"
          onClick={() => {
            setFilter("measure", "")
            setFilter("tier", "")
            setFilter("role", "")
          }}
        >
          Clear
        </button>
      ) : null}
    </div>
  )
}
