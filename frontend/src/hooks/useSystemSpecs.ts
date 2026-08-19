/** Loads system specs and model recommendations together. */
import { useCallback, useEffect, useState } from 'react'

import { getRecommendations } from '../api/client'
import type { RecommendationsResponse } from '../types/api'

interface UseSystemSpecsResult {
  data: RecommendationsResponse | null
  loading: boolean
  error: string | null
  /** Re-run hardware detection, bypassing the backend's cache. */
  rescan: () => void
}

export function useSystemSpecs(enabled: boolean): UseSystemSpecsResult {
  const [data, setData] = useState<RecommendationsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!enabled) return

    let cancelled = false
    setLoading(true)
    setError(null)

    getRecommendations(3, nonce > 0)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to detect hardware')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [enabled, nonce])

  const rescan = useCallback(() => setNonce((n) => n + 1), [])

  return { data, loading, error, rescan }
}
