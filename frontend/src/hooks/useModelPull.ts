/** Drives a model download and exposes live progress. */
import { useCallback, useEffect, useRef, useState } from 'react'

import { StreamAbortedError, streamSse } from '../api/sseClient'
import type { PullProgress } from '../types/api'

export type PullState = 'idle' | 'downloading' | 'done' | 'error' | 'cancelled'

interface UseModelPullResult {
  state: PullState
  progress: PullProgress | null
  error: string | null
  start: (model: string) => void
  cancel: () => void
  reset: () => void
}

export function useModelPull(): UseModelPullResult {
  const [state, setState] = useState<PullState>('idle')
  const [progress, setProgress] = useState<PullProgress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  // Abort any in-flight download if the component unmounts.
  useEffect(() => {
    return () => controllerRef.current?.abort()
  }, [])

  const start = useCallback((model: string) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    setState('downloading')
    setProgress(null)
    setError(null)

    void (async () => {
      try {
        for await (const event of streamSse(
          '/api/models/pull',
          { model },
          { signal: controller.signal },
        )) {
          if (event.event === 'progress') {
            setProgress(event.data)
          } else if (event.event === 'done') {
            setState('done')
            return
          } else if (event.event === 'error') {
            setError(event.data.message)
            setState('error')
            return
          }
        }
        // Stream closed without an explicit terminator.
        setState('done')
      } catch (err) {
        if (err instanceof StreamAbortedError || controller.signal.aborted) {
          setState('cancelled')
          return
        }
        setError(err instanceof Error ? err.message : 'Download failed')
        setState('error')
      }
    })()
  }, [])

  const cancel = useCallback(() => {
    controllerRef.current?.abort()
    setState('cancelled')
  }, [])

  const reset = useCallback(() => {
    controllerRef.current?.abort()
    setState('idle')
    setProgress(null)
    setError(null)
  }, [])

  return { state, progress, error, start, cancel, reset }
}
