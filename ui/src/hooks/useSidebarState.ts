import { useCallback, useEffect, useState, useSyncExternalStore } from "react"

import { usePersistedFlag } from "./usePersistedFlag"

const NARROW_QUERY = "(max-width: 899px)"
const WIDE_OPEN_KEY = "hedis.sidebarOpen"

const subscribeNarrow = (onChange: () => void): (() => void) => {
  const query = window.matchMedia(NARROW_QUERY)
  query.addEventListener("change", onChange)
  return () => {
    query.removeEventListener("change", onChange)
  }
}

const readNarrow = (): boolean => window.matchMedia(NARROW_QUERY).matches

interface SidebarState {
  open: boolean
  isNarrow: boolean
  toggle: () => void
  closeOverlay: () => void
}

/** Wide screens: a remembered show/hide choice. Narrow screens: an overlay that starts closed. */
export function useSidebarState(): SidebarState {
  const isNarrow = useSyncExternalStore(subscribeNarrow, readNarrow)
  const [wideOpen, setWideOpen] = usePersistedFlag(WIDE_OPEN_KEY, true)
  const [overlayOpen, setOverlayOpen] = useState<boolean>(false)

  const toggle = useCallback((): void => {
    if (isNarrow) {
      setOverlayOpen((current) => !current)
    } else {
      setWideOpen(!wideOpen)
    }
  }, [isNarrow, setWideOpen, wideOpen])

  const closeOverlay = useCallback((): void => {
    setOverlayOpen(false)
  }, [])

  useEffect(() => {
    if (!isNarrow || !overlayOpen) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setOverlayOpen(false)
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => {
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [isNarrow, overlayOpen])

  return { open: isNarrow ? overlayOpen : wideOpen, isNarrow, toggle, closeOverlay }
}
