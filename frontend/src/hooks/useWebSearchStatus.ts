/**
 * Whether web search can work, and which provider is serving it.
 *
 * Polls slowly until the built-in SearXNG reports ready, then caches and stops.
 * The first run installs SearXNG in the background, which takes minutes, and the
 * status label should upgrade itself from "public search" to "private search"
 * without the user reloading.
 *
 * Defaults to available while the first probe is in flight, so the toggle is
 * usable immediately rather than disabled for a beat and then enabled.
 */
import { useEffect, useState } from 'react'

import { getWebSearchStatus } from '../api/client'
import type { WebSearchStatus } from '../types/api'

let cached: WebSearchStatus | null = null

// Slow: this is a background upgrade, not something the user waits on.
const POLL_INTERVAL_MS = 15000

const OPTIMISTIC: WebSearchStatus = {
  available: true,
  provider: '',
  searxng_detected: false,
  detail: null,
}

export function useWebSearchStatus(): WebSearchStatus {
  const [status, setStatus] = useState<WebSearchStatus>(cached ?? OPTIMISTIC)

  useEffect(() => {
    // A settled answer is cached for the session; only a provisional one keeps
    // polling.
    if (cached && cached.provider === 'searxng') {
      setStatus(cached)
      return
    }

    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined

    const check = () => {
      void getWebSearchStatus()
        .then((result) => {
          cached = result
          if (!active) return
          setStatus(result)
          // The built-in instance can take a couple of minutes to install on
          // first run, and the label should flip from "public search" to
          // "private search" on its own rather than after a reload.
          if (result.provider !== 'searxng') {
            timer = setTimeout(check, POLL_INTERVAL_MS)
          }
        })
        .catch(() => {
          // A failed probe should not disable a toggle that may well work; try
          // again shortly.
          if (active) timer = setTimeout(check, POLL_INTERVAL_MS)
        })
    }

    check()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [])

  return status
}
