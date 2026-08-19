/**
 * Warms a model into Ollama's memory ahead of the first message, and tracks
 * which model is currently loaded so the connection is kept for the whole
 * app session instead of being re-established every time a chat screen
 * mounts (new chat, reopening a conversation, etc.).
 *
 * Without this, the "connecting" cost (reading the model off disk into
 * RAM/VRAM) would either silently extend the first reply, or - if triggered
 * naively on every mount - reconnect on every navigation even when nothing
 * actually changed. Call `ensureLoaded(model)`; it only talks to the backend
 * when `model` differs from whatever is already loaded or in flight.
 */
import { useCallback, useRef, useState } from 'react'

import { preloadModel } from '../api/client'

export type PreloadState = 'idle' | 'loading' | 'ready' | 'error'

interface UseModelPreloadResult {
  state: PreloadState
  error: string | null
  /** The model currently loaded and ready, or null if none/still loading. */
  loadedModel: string | null
  /** The model `state`/`error` actually describes - compare against your own
   * current model before trusting a 'loading' or 'error' state, since this is
   * shared app-wide and may reflect a different chat's in-flight switch. */
  targetModel: string | null
  /** Loads `model` only if it isn't already loaded or already loading. */
  ensureLoaded: (model: string) => void
}

export function useModelPreload(): UseModelPreloadResult {
  const [state, setState] = useState<PreloadState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [loadedModel, setLoadedModel] = useState<string | null>(null)
  const [targetModel, setTargetModel] = useState<string | null>(null)
  const inFlightModel = useRef<string | null>(null)

  const ensureLoaded = useCallback(
    (model: string) => {
      if (loadedModel === model || inFlightModel.current === model) {
        return
      }

      inFlightModel.current = model
      setTargetModel(model)
      setState('loading')
      setError(null)

      void preloadModel(model)
        .then(() => {
          // Ignore a stale response if the user switched again before this
          // request settled - only the most recent request may land.
          if (inFlightModel.current === model) {
            setLoadedModel(model)
            setState('ready')
          }
        })
        .catch((err: unknown) => {
          if (inFlightModel.current === model) {
            setError(err instanceof Error ? err.message : 'Failed to load model')
            setState('error')
          }
        })
    },
    [loadedModel],
  )

  return { state, error, loadedModel, targetModel, ensureLoaded }
}
