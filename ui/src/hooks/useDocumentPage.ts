import { useEffect, useState } from "react"

import type { DocumentPageRef, EvidenceDocumentPage } from "../models/evidence"
import { loadDocumentPage, loadDocumentPageImage } from "../services/evidence.service"
import { toRequestErrorMessage } from "../services/http"

interface UseDocumentPageResult {
  page: EvidenceDocumentPage | null
  imageUrl: string | null
  loading: boolean
  error: string | null
}

/** One stored page and, when it has one, its image as an object URL that is revoked on change and unmount. */
export function useDocumentPage(ref: DocumentPageRef | null): UseDocumentPageResult {
  const [page, setPage] = useState<EvidenceDocumentPage | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  const documentId = ref?.documentId ?? ""
  const pageNo = ref?.pageNo ?? 0

  useEffect(() => {
    if (!documentId || pageNo <= 0) {
      return
    }
    let cancelled = false
    let objectUrl: string | null = null

    const load = async (): Promise<void> => {
      setLoading(true)
      setError(null)
      setPage(null)
      setImageUrl(null)
      try {
        const nextPage = await loadDocumentPage(documentId, pageNo)
        if (cancelled) {
          return
        }
        setPage(nextPage)
        if (nextPage.hasImage) {
          const blob = await loadDocumentPageImage(documentId, pageNo)
          if (cancelled) {
            return
          }
          objectUrl = URL.createObjectURL(blob)
          setImageUrl(objectUrl)
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(toRequestErrorMessage(loadError))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void load()

    return () => {
      cancelled = true
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [documentId, pageNo])

  return { page, imageUrl, loading, error }
}
