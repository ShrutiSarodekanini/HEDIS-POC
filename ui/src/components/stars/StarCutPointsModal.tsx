import { useState } from "react"

import type { StarCatalogMeasure } from "../../models/stars"
import { Modal } from "../layout/Modal"
import { StarCutPoints } from "./StarCutPoints"
import { sortByMeasureId } from "./starFormat"

interface StarCutPointsModalProps {
  measures: StarCatalogMeasure[] | undefined
  initialMeasureId: string
  onClose: () => void
}

export function StarCutPointsModal({ measures, initialMeasureId, onClose }: StarCutPointsModalProps) {
  const [measureFilter, setMeasureFilter] = useState<string>(initialMeasureId)

  return (
    <Modal title="Star cut points" subtitle="Star bands by measure" onClose={onClose}>
      <label className="calc-check star-cut-filter">
        <span>Measure</span>
        <select
          className="text-input"
          value={measureFilter}
          onChange={(event) => {
            setMeasureFilter(event.target.value)
          }}
        >
          <option value="">All measures</option>
          {sortByMeasureId(measures ?? []).map((measure) => (
            <option key={measure.measureId} value={measure.measureId}>
              {measure.measureId} - {measure.measureName}
            </option>
          ))}
        </select>
      </label>

      <StarCutPoints measures={measures} measureFilter={measureFilter} bare />
    </Modal>
  )
}
