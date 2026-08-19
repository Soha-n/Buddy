/**
 * Owns one conversation's live messages and streaming state.
 *
 * History is model-agnostic: switching the current model mid-chat keeps every
 * prior message and just changes which model answers the next turn. The
 * backend persists both halves of each turn itself (see routers/chat.py), so
 * this hook's job is purely the live view plus telling the caller the
 * conversation id once the backend creates or confirms one.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { StreamAbortedError, streamSse } from '../api/sseClient'
import type {
  AttachmentRecord,
  ChatMessage,
  MessageRecord,
  SearchCitation,
} from '../types/api'

/** Sentinel distinct from any real id or messages array; see its use below. */
const NEVER_APPLIED = Symbol('never-applied')

export interface ChatStats {
  tokensPerSec: number | null
  evalCount: number
}

export interface DisplayMessage extends ChatMessage {
  modelUsedForThisTurn?: string | null
  /** Files sent with this turn, so the transcript can show them inline. */
  attachments?: AttachmentRecord[]
  /** Web sources consulted for this reply, shown above the answer. */
  citations?: SearchCitation[]
}

interface UseChatOptions {
  conversationId: string | null
  initialMessages?: MessageRecord[]
  onConversationCreated?: (id: string) => void
}

interface UseChatResult {
  messages: DisplayMessage[]
  streaming: boolean
  error: string | null
  lastStats: ChatStats | null
  send: (
    content: string,
    model: string,
    attachments?: AttachmentRecord[],
    webSearch?: boolean,
  ) => void
  stop: () => void
}

function fromRecords(records: MessageRecord[]): DisplayMessage[] {
  return records.map((r) => ({
    role: r.role,
    content: r.content,
    modelUsedForThisTurn: r.model_used_for_this_turn,
    attachments: r.attachments ?? [],
  }))
}

export function useChat(options: UseChatOptions): UseChatResult {
  const { conversationId, initialMessages, onConversationCreated } = options
  const [messages, setMessages] = useState<DisplayMessage[]>(
    initialMessages ? fromRecords(initialMessages) : [],
  )
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastStats, setLastStats] = useState<ChatStats | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  // A send() while conversationId is still null adopts the id the backend
  // hands back via the meta event (see below), which flows back in as a prop
  // change. That is NOT a conversation switch - it is this same in-flight
  // conversation gaining its id - so it must not wipe the messages the stream
  // is actively writing into. Only a switch to a genuinely different,
  // previously-known id (or explicitly back to null for "new chat") resets.
  const adoptedIdRef = useRef<string | null>(null)

  // Remembers the exact initialMessages array last applied to `messages`, so
  // a history fetch that resolves *after* mount (useConversation's fetch is
  // async - the first render of a freshly-opened conversation typically has
  // initialMessages still undefined) is picked up once it arrives, while a
  // conversation with genuinely no history yet (a brand new one) does not
  // loop re-applying an empty reset every render. Starting both at the
  // module-level NEVER_APPLIED sentinel (rather than the real initial
  // conversationId/initialMessages) guarantees the very first effect run
  // always applies once, which matters when a component mounts directly with
  // a non-null id and no history yet.
  const appliedInitialMessagesRef = useRef<MessageRecord[] | undefined | typeof NEVER_APPLIED>(
    NEVER_APPLIED,
  )
  const appliedForIdRef = useRef<string | null | typeof NEVER_APPLIED>(NEVER_APPLIED)

  useEffect(() => {
    if (conversationId !== null && conversationId === adoptedIdRef.current) {
      // Just adopted from our own in-flight send - not a switch, so the live
      // messages this hook is actively writing into must be left alone.
      appliedForIdRef.current = conversationId
      appliedInitialMessagesRef.current = initialMessages
      return
    }

    const isSameConversation = appliedForIdRef.current === conversationId
    const historyAlreadyApplied = appliedInitialMessagesRef.current === initialMessages
    if (isSameConversation && historyAlreadyApplied) {
      return
    }

    // A null id can never legitimately have history - reset to empty rather
    // than trusting `initialMessages`, which can still hold the *previous*
    // conversation's data for one render: useConversation(null) clears its
    // fetched detail in its own effect, which can lag one tick behind the id
    // prop actually reaching null here.
    setMessages(conversationId === null || !initialMessages ? [] : fromRecords(initialMessages))
    setError(null)
    setLastStats(null)
    adoptedIdRef.current = null
    appliedForIdRef.current = conversationId
    appliedInitialMessagesRef.current = initialMessages
  }, [conversationId, initialMessages])

  useEffect(() => {
    return () => controllerRef.current?.abort()
  }, [])

  const send = useCallback(
    (
      content: string,
      model: string,
      attachments: AttachmentRecord[] = [],
      webSearch = false,
    ) => {
      const trimmed = content.trim()
      if (!trimmed || streaming) return

      // Attachments ride along on the optimistic user turn so they appear in
      // the transcript the instant the message is sent, rather than only after
      // the conversation is reloaded from the backend.
      const userMessage: DisplayMessage = {
        role: 'user',
        content: trimmed,
        attachments,
      }
      // Only role and content go on the wire: the backend reaches attachment
      // contents through retrieval, so re-sending them per turn would be waste.
      const outgoing: ChatMessage[] = [...messages, userMessage].map((m) => ({
        role: m.role,
        content: m.content,
      }))

      setMessages((current) => [
        ...current,
        userMessage,
        { role: 'assistant', content: '', modelUsedForThisTurn: model },
      ])
      setStreaming(true)
      setError(null)
      setLastStats(null)

      const controller = new AbortController()
      controllerRef.current = controller

      void (async () => {
        try {
          for await (const event of streamSse(
            '/api/chat',
            {
              model,
              messages: outgoing,
              conversation_id: conversationId,
              attachment_ids: attachments.map((a) => a.id),
              web_search: webSearch,
            },
            { signal: controller.signal },
          )) {
            if (event.event === 'meta') {
              adoptedIdRef.current = event.data.conversation_id
              onConversationCreated?.(event.data.conversation_id)
            } else if (event.event === 'sources') {
              // Arrives before the first token, so the sources panel is already
              // on screen while the model is still generating.
              const { citations } = event.data
              setMessages((current) => {
                const next = [...current]
                const last = next[next.length - 1]
                if (last && last.role === 'assistant') {
                  next[next.length - 1] = { ...last, citations }
                }
                return next
              })
            } else if (event.event === 'token') {
              const chunk = event.data.content
              setMessages((current) => {
                const next = [...current]
                const last = next[next.length - 1]
                if (last && last.role === 'assistant') {
                  next[next.length - 1] = { ...last, content: last.content + chunk }
                }
                return next
              })
            } else if (event.event === 'done') {
              const data = event.data as {
                tokens_per_sec?: number | null
                eval_count?: number
              }
              setLastStats({
                tokensPerSec: data.tokens_per_sec ?? null,
                evalCount: data.eval_count ?? 0,
              })
              return
            } else if (event.event === 'error') {
              setError(event.data.message)
              dropEmptyAssistantTurn(setMessages)
              return
            }
          }
        } catch (err) {
          if (err instanceof StreamAbortedError || controller.signal.aborted) {
            dropEmptyAssistantTurn(setMessages)
            return
          }
          // A 422 from the vision gate arrives here; its detail already names
          // the models that would work, so it is shown as-is.
          setError(err instanceof Error ? err.message : 'Chat request failed')
          dropEmptyAssistantTurn(setMessages)
        } finally {
          setStreaming(false)
          controllerRef.current = null
        }
      })()
    },
    [conversationId, messages, onConversationCreated, streaming],
  )

  const stop = useCallback(() => {
    controllerRef.current?.abort()
    setStreaming(false)
  }, [])

  return { messages, streaming, error, lastStats, send, stop }
}

/** Remove a trailing assistant turn that never received any tokens. */
function dropEmptyAssistantTurn(
  setMessages: React.Dispatch<React.SetStateAction<DisplayMessage[]>>,
): void {
  setMessages((current) => {
    const last = current[current.length - 1]
    if (last && last.role === 'assistant' && last.content === '') {
      return current.slice(0, -1)
    }
    return current
  })
}
