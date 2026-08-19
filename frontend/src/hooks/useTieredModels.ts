/** Fetches the Best/Better/Good tiers once, shared across all three tab views. */
import { useCallback, useEffect, useState } from 'react'

import { getTiers } from '../api/client'
import type { TiersResponse } from '../types/api'

interface UseTieredModelsResult {
  data: TiersResponse | null
  loading: boolean
  error: string | null
  rescan: () => void
}

export function useTieredModels(enabled: boolean): UseTieredModelsResult {
  const [data, setData] = useState<TiersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    setLoading(true)
    setError(null)

    getTiers(nonce > 0)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to score models')
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
