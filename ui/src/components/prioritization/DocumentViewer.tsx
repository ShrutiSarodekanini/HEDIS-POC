import { useDocumentPage } from "../../hooks/useDocumentPage"
import type { DocumentPageRef } from "../../models/evidence"

const METHOD_LABELS: Record<string, string> = {
  ocr_printed: "OCR printed",
  ocr_handwriting: "OCR handwritten",
  plain_text: "Plain text",
  text_layer: "Text layer",
  vision: "Vision",
}

function HighlightedText({ text, quote }: { text: string; quote: string | null }) {
  const needle = (quote ?? "").trim()
  const start = needle ? text.toLowerCase().indexOf(needle.toLowerCase()) : -1
  if (start < 0) {
    return <pre className="doc-text">{text || "This page has no extracted text."}</pre>
  }
  const end = start + needle.length
  return (
    <pre className="doc-text">
      {text.slice(0, start)}
      <mark>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </pre>
  )
}

interface DocumentViewerProps {
  pageRef: DocumentPageRef
  onBack: () => void
}

export function DocumentViewer({ pageRef, onBack }: DocumentViewerProps) {
  const { page, imageUrl, loading, error } = useDocumentPage(pageRef)
  const method = page ? METHOD_LABELS[page.method] ?? page.method : "-"
  const quoteFound = Boolean(
    page && pageRef.quote && page.text.toLowerCase().includes(pageRef.quote.trim().toLowerCase()),
  )

  return (
    <div className="doc-viewer">
      <div className="doc-viewer-head">
        <button type="button" className="btn outline" onClick={onBack}>
          Back
        </button>
        <span className="note">
          Document {pageRef.documentId} | Page {pageRef.pageNo} | {method}
          {page?.handwritten ? " | handwritten" : ""}
        </span>
      </div>

      {loading ? <p className="card-message">Loading page...</p> : null}
      {error ? <p className="card-message error">{error}</p> : null}

      {page ? (
        <>
          {imageUrl ? (
            <img className="doc-image" src={imageUrl} alt={`Document ${pageRef.documentId} page ${pageRef.pageNo}`} />
          ) : null}
          {pageRef.quote && !quoteFound ? (
            <p className="note">The quoted passage was not found word for word in this page's text.</p>
          ) : null}
          <HighlightedText text={page.text} quote={pageRef.quote} />
        </>
      ) : null}
    </div>
  )
}
