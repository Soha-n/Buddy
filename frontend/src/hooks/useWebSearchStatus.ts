/**
 * Whether web search can work, and which provider would serve it.
 *
 * Fetched once per app session and cached at module level: the answer depends on
 * this machine's network and whether a SearXNG instance is running, neither of
 * which changes between messages. Without the cache every ChatScreen remount
 * would re-probe the network.
 *
 * Defaults to available-with-no-detail while the probe is in flight, so the
 * toggle is usable immediately rather than disabled for a beat and then enabled.
 * A genuinely unavailable provider is caught by the backend, which reports the
 * failure as a normal chat error.
 */
import { useEffect, useState } from 'react'

import { getWebSearchStatus } from '../api/client'
import type { WebSearchStatus } from '../types/api'

let cached: WebSearchStatus | null = null

const OPTIMISTIC: WebSearchStatus = {
  available: true,
  provider: '',
  searxng_detected: false,
  detail: null,
}

export function useWebSearchStatus(): WebSearchStatus {
  const [status, setStatus] = useState<WebSearchStatus>(cached ?? OPTIMISTIC)

  useEffect(() => {
    if (cached) return
    let active = true
    getWebSearchStatus()
      .then((result) => {
        cached = result
        if (active) setStatus(result)
      })
      .catch(() => {
        // Leaving the optimistic default in place: a failed status probe should
        // not disable a toggle that may well work.
      })
    return () => {
      active = false
    }
  }, [])

  return status
}
