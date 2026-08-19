/** Polls backend + Ollama availability. */
import { useCallback, useEffect, useState } from 'react'

import { getHealth } from '../api/client'
import type { HealthResponse } from '../types/api'

interface UseHealthResult {
  health: HealthResponse | null
  loading: boolean
  error: string | null
  recheck: () => void
}

export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getHealth()
      .then((result) => {
        if (!cancelled) setHealth(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Backend unreachable')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [nonce])

  const recheck = useCallback(() => setNonce((n) => n + 1), [])

  return { health, loading, error, recheck }
}
