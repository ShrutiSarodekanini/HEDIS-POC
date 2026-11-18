import { useCallback, useState } from "react"

import { waitForCalculation } from "../services/calculation.service"
import { loadGapReviewStatus, requestGapRerun } from "../services/evidence.service"
import { toRequestErrorMessage } from "../services/http"

interface UseGapRerunResult {
  running: boolean
  finalStatus: string | null
  outcome: string | null
  error: string | null
  rerun: () => Promise<void>
}

const ENGINE_FINAL = new Set(["closed", "excluded"])

/**
 * Reruns the measure engine for one gap with its confirmed evidence and reports what the engine decided. A person
 * never closes a gap: the engine closes it when the measure is met or the patient excluded. onChanged lets the
 * parent reload its data.
 */
export function useGapRerun(gapId: string, onChanged: () => void): UseGapRerunResult {
  const [running, setRunning] = useState<boolean>(false)
  const [finalStatus, setFinalStatus] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rerun = useCallback(async (): Promise<void> => {
    setRunning(true)
    setError(null)
    setOutcome(null)
    try {
      const started = await requestGapRerun(gapId)
      const finished = await waitForCalculation(started.batchId)
      if (!finished.success) {
        throw new Error(finished.message)
      }
      const status = await loadGapReviewStatus(gapId)
      const skipped = started.notUsable.length
        ? ` ${started.notUsable.length} confirmed finding(s) could not be sent to the engine.`
        : ""
      if (ENGINE_FINAL.has(status)) {
        setFinalStatus(status)
        setOutcome(
          status === "excluded"
            ? `The engine excluded this patient and closed the gap.${skipped}`
            : `The engine found the measure met and closed the gap.${skipped}`,
        )
      } else {
        setOutcome(`The engine ran and the measure is still not met, so the gap stays open.${skipped}`)
      }
      onChanged()
    } catch (rerunError) {
      setError(toRequestErrorMessage(rerunError))
    } finally {
      setRunning(false)
    }
  }, [gapId, onChanged])

  return { running, finalStatus, outcome, error, rerun }
}
