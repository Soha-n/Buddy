/**
 * Runs one chart code block on demand and holds its result.
 *
 * Keyed by code string rather than by message index: a code block's identity is
 * its content, and message indices shift as the transcript grows, which would
 * make a rendered chart jump to the wrong block after the next reply.
 *
 * Nothing runs without an explicit call from a click handler - there is no
 * effect that fires on render. That is the whole point of the approval step.
 */
import { useCallback, useState } from 'react'

import { runChartCode } from '../api/client'
import type { RunCodeResponse } from '../types/api'

export interface ChartRunState {
  running: boolean
  result: RunCodeResponse | null
}

const IDLE: ChartRunState = { running: false, result: null }

interface UseChartRunnerResult {
  stateFor: (code: string) => ChartRunState
  run: (code: string) => void
}

export function useChartRunner(conversationId: string | null): UseChartRunnerResult {
  const [states, setStates] = useState<Record<string, ChartRunState>>({})

  const run = useCallback(
    (code: string) => {
      setStates((current) => {
        if (current[code]?.running) return current
        return { ...current, [code]: { running: true, result: null } }
      })

      void runChartCode(code, conversationId)
        .then((result) => {
          setStates((current) => ({ ...current, [code]: { running: false, result } }))
        })
        .catch((err: unknown) => {
          // A transport failure is presented the same way as a rejected script,
          // so the UI has one error path instead of two.
          setStates((current) => ({
            ...current,
            [code]: {
              running: false,
              result: {
                ok: false,
                stdout: '',
                error: err instanceof Error ? err.message : 'Could not run the code',
                image_base64: null,
                duration_s: 0,
              },
            },
          }))
        })
    },
    [conversationId],
  )

  const stateFor = useCallback(
    (code: string): ChartRunState => states[code] ?? IDLE,
    [states],
  )

  return { stateFor, run }
}
