import { useCallback, useEffect, useRef, useState } from "react"

import type { LatestBatch } from "../models/calculation"
import { fetchIngestionAwaitingBatch, fetchLatestBatch } from "../services/calculation.service"
import { toRequestErrorMessage } from "../services/http"

const POLL_MS = 5000

export interface UseLatestBatchResult {
  batch: LatestBatch | null
  awaitingBatch: boolean
  loading: boolean
  error: string | null
}

/**
 * Reads the latest batch and polls while it runs. Calls onProgress when the same batch moves to another step,
 * and onFinished once when it stops running.
 */
export function useLatestBatch(onFinished: () => void, onProgress: () => void): UseLatestBatchResult {
  const [batch, setBatch] = useState<LatestBatch | null>(null)
  const [awaitingBatch, setAwaitingBatch] = useState<boolean>(false)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  const previousRef = useRef<{ batchId: string | null; status: string | null; step: string | null } | null>(null)
  const onFinishedRef = useRef(onFinished)
  const onProgressRef = useRef(onProgress)

  useEffect(() => {
    onFinishedRef.current = onFinished
  }, [onFinished])

  useEffect(() => {
    onProgressRef.current = onProgress
  }, [onProgress])

  const load = useCallback(async (): Promise<void> => {
    try {
      const [nextBatch, nextAwaiting] = await Promise.all([
        fetchLatestBatch(),
        fetchIngestionAwaitingBatch(),
      ])
      setBatch(nextBatch)
      setAwaitingBatch(nextAwaiting)
      setError(null)

      const previous = previousRef.current
      previousRef.current = {
        batchId: nextBatch?.batchId ?? null,
        status: nextBatch?.status ?? null,
        step: nextBatch?.step ?? null,
      }
      // A new batch can start and finish between two polls, so a changed batch id also counts.
      const stoppedRunning = previous?.status === "running" || (previous !== null && previous.batchId !== nextBatch?.batchId)
      const movedStep = previous !== null && nextBatch !== null && previous.batchId === nextBatch.batchId
        && previous.step !== nextBatch.step
      if (nextBatch && nextBatch.status !== "running" && stoppedRunning) {
        onFinishedRef.current()
      } else if (movedStep) {
        onProgressRef.current()
      }
    } catch (loadError) {
      setError(toRequestErrorMessage(loadError))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Scheduled rather than called inline; the cleanup also drops the duplicate StrictMode mount.
    const timer = window.setTimeout(() => {
      void load()
    }, 0)
    return () => {
      window.clearTimeout(timer)
    }
  }, [load])

  // An ingestion job without its batch yet is about to start one, so keep watching until it does.
  const shouldPoll = batch?.status === "running" || awaitingBatch

  useEffect(() => {
    if (!shouldPoll) {
      return
    }
    const timer = window.setInterval(() => {
      void load()
    }, POLL_MS)
    return () => {
      window.clearInterval(timer)
    }
  }, [load, shouldPoll])

  return { batch, awaitingBatch, loading, error }
}
