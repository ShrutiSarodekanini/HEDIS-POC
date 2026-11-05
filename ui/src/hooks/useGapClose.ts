import { useCallback, useState } from "react"

import { closeCareGap } from "../services/evidence.service"
import { toRequestErrorMessage } from "../services/http"

interface UseGapCloseResult {
  closing: boolean
  closedStatus: string | null
  error: string | null
  closeGap: () => Promise<void>
}

/** Closes one gap through the API and reports the new review status; onClosed lets the parent reload its data. */
export function useGapClose(gapId: string, onClosed: () => void): UseGapCloseResult {
  const [closing, setClosing] = useState<boolean>(false)
  const [closedStatus, setClosedStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const closeGap = useCallback(async (): Promise<void> => {
    setClosing(true)
    setError(null)
    try {
      const result = await closeCareGap(gapId)
      setClosedStatus(result.reviewStatus)
      onClosed()
    } catch (closeError) {
      setError(toRequestErrorMessage(closeError))
    } finally {
      setClosing(false)
    }
  }, [gapId, onClosed])

  return { closing, closedStatus, error, closeGap }
}
