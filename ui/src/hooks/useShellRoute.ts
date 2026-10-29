import { useEffect, useState } from "react"

export type AppView = "dashboard" | "ingestion" | "calculation" | "quality" | "stars" | "prioritization" | "evidence"

const fromHash = (hash: string): AppView => {
  if (hash === "#view-ingestion") {
    return "ingestion"
  }
  if (hash === "#view-calculation") {
    return "calculation"
  }
  if (hash === "#view-quality") {
    return "quality"
  }
  if (hash === "#view-stars") {
    return "stars"
  }
  if (hash === "#view-prioritization") {
    return "prioritization"
  }
  if (hash === "#view-evidence") {
    return "evidence"
  }
  return "dashboard"
}

export function useShellRoute(): AppView {
  const [view, setView] = useState<AppView>(fromHash(window.location.hash))

  useEffect(() => {
    const handler = () => {
      setView(fromHash(window.location.hash))
    }

    window.addEventListener("hashchange", handler)
    return () => {
      window.removeEventListener("hashchange", handler)
    }
  }, [])

  return view
}
