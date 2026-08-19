/**
 * The location Buddy uses for "here" questions.
 *
 * Everything comes from the user's own machine - no IP geolocation service, so
 * nothing about their whereabouts leaves the device. Never probes on mount:
 * opening a settings panel should not raise an OS permission prompt, so precise
 * detection is always a deliberate click.
 */
import { useCallback, useEffect, useState } from 'react'

import { clearLocation, getLocation, setLocation } from '../api/client'
import type { LocationResponse } from '../types/api'

interface UseLocationResult {
  location: LocationResponse | null
  busy: boolean
  error: string | null
  /** Country from OS settings. No permission prompt. */
  detect: () => void
  /** Exact coordinates from the OS location service - raises a consent dialog,
   *  so it is only ever called from a click. */
  detectPrecise: () => void
  save: (city: string, region?: string, country?: string) => void
  reset: () => void
}

export function useLocation(): UseLocationResult {
  const [location, setLocationState] = useState<LocationResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback((work: () => Promise<LocationResponse>) => {
    setBusy(true)
    setError(null)
    work()
      .then(setLocationState)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : 'Could not update location'),
      )
      .finally(() => setBusy(false))
  }, [])

  // Reads the cached value only - no probe, no prompt.
  useEffect(() => {
    getLocation(false, false)
      .then(setLocationState)
      .catch(() => {
        /* absent location is a normal state, not an error worth showing */
      })
  }, [])

  return {
    location,
    busy,
    error,
    detect: useCallback(() => run(() => getLocation(true, false)), [run]),
    detectPrecise: useCallback(() => run(() => getLocation(false, true)), [run]),
    save: useCallback(
      (city: string, region?: string, country?: string) =>
        run(() => setLocation(city, region, country)),
      [run],
    ),
    reset: useCallback(() => run(() => clearLocation()), [run]),
  }
}
