/** Sidebar conversation list: fetch, create, rename, delete. */
import { useCallback, useEffect, useState } from 'react'

import {
  createConversation as apiCreateConversation,
  deleteConversation as apiDeleteConversation,
  listConversations,
  renameConversation as apiRenameConversation,
} from '../api/client'
import type { ConversationSummary } from '../types/api'

interface UseConversationsResult {
  conversations: ConversationSummary[]
  loading: boolean
  /** True once the first fetch has settled (success or failure). Distinct
   * from `!loading`, which is also true before `enabled` ever turns on and
   * the fetch has not even started - callers that need to know "is this data
   * trustworthy yet" should check this, not just the absence of loading. */
  hasLoadedOnce: boolean
  error: string | null
  refresh: () => void
  createConversation: () => Promise<ConversationSummary>
  renameConversation: (id: string, title: string) => Promise<void>
  deleteConversation: (id: string) => Promise<void>
}

export function useConversations(enabled: boolean): UseConversationsResult {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    setLoading(true)

    listConversations()
      .then((result) => {
        if (!cancelled) {
          setConversations(result)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load conversations')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
          setHasLoadedOnce(true)
        }
      })

    return () => {
      cancelled = true
    }
  }, [enabled, nonce])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  const createConversation = useCallback(async () => {
    const created = await apiCreateConversation()
    setConversations((current) => [created, ...current])
    return created
  }, [])

  const renameConversation = useCallback(async (id: string, title: string) => {
    const updated = await apiRenameConversation(id, title)
    setConversations((current) =>
      current.map((c) => (c.id === id ? updated : c)),
    )
  }, [])

  const deleteConversation = useCallback(async (id: string) => {
    await apiDeleteConversation(id)
    setConversations((current) => current.filter((c) => c.id !== id))
  }, [])

  return {
    conversations,
    loading,
    hasLoadedOnce,
    error,
    refresh,
    createConversation,
    renameConversation,
    deleteConversation,
  }
}
