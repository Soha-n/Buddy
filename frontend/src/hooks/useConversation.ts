/** Loads one conversation's full message history when its id changes. */
import { useEffect, useState } from 'react'

import { getConversation } from '../api/client'
import type { ConversationDetail } from '../types/api'

interface UseConversationResult {
  conversation: ConversationDetail | null
  loading: boolean
  error: string | null
}

export function useConversation(id: string | null): UseConversationResult {
  const [conversation, setConversation] = useState<ConversationDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) {
      setConversation(null)
      setError(null)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    getConversation(id)
      .then((result) => {
        if (!cancelled) setConversation(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load conversation')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [id])

  return { conversation, loading, error }
}
