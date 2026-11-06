import { useCallback, useState } from "react"

const readFlag = (key: string, fallback: boolean): boolean => {
  try {
    const stored = localStorage.getItem(key)
    return stored === null ? fallback : stored === "true"
  } catch {
    // Storage can be blocked (strict privacy mode); the default still works for this visit.
    return fallback
  }
}

/** A boolean UI preference kept in localStorage, falling back to memory when storage is unavailable. */
export function usePersistedFlag(key: string, fallback: boolean): [boolean, (next: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => readFlag(key, fallback))

  const update = useCallback((next: boolean): void => {
    setValue(next)
    try {
      localStorage.setItem(key, String(next))
    } catch {
      // Keep the in-memory value when storage is blocked.
    }
  }, [key])

  return [value, update]
}
