/**
 * Owns the files staged in the composer for the *next* message.
 *
 * The staging area is deliberately short-lived. Files sit here only until the
 * message they belong to is sent; `clearStaged()` then empties the composer and
 * the files live on inside that turn in the transcript. They are never
 * re-attached to later messages - the chat already knows about them, because the
 * backend links each upload to the message it was sent with and reaches their
 * contents through retrieval. Leaving chips in the box after sending would imply
 * every following question re-uploads them.
 *
 * A conversation has to exist before files can be attached to it, but the app
 * lets the user attach in a brand-new chat that has no id yet. So an upload into
 * a null conversation creates the conversation first, then reports the new id
 * upward - the same adoption path a first message takes.
 *
 * Indexing finishes after the upload response, so anything still 'pending' is
 * polled until it settles, and the interval is cleared the moment nothing is.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  createConversation,
  deleteAttachment as deleteAttachmentRequest,
  listAttachments,
  uploadAttachments,
} from '../api/client'
import type { AttachmentRecord } from '../types/api'

const POLL_INTERVAL_MS = 1200

interface UseAttachmentsOptions {
  conversationId: string | null
  /** Called when an upload had to create the conversation itself. */
  onConversationCreated?: (id: string) => void
}

interface UseAttachmentsResult {
  /** Files staged for the next message only. Empty after a send. */
  staged: AttachmentRecord[]
  uploading: boolean
  error: string | null
  upload: (files: File[]) => Promise<void>
  /** Remove one staged file, deleting it server-side since it was never sent. */
  remove: (attachmentId: string) => void
  /** Called after a successful send: the files now belong to that turn. */
  clearStaged: () => void
  clearError: () => void
}

export function useAttachments(options: UseAttachmentsOptions): UseAttachmentsResult {
  const { conversationId, onConversationCreated } = options
  const [staged, setStaged] = useState<AttachmentRecord[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Guards against a late poll from a previous conversation landing after the
  // user has navigated away.
  const activeConversationRef = useRef<string | null>(conversationId)
  useEffect(() => {
    activeConversationRef.current = conversationId
  }, [conversationId])

  // Switching conversations abandons whatever was staged: those files belong to
  // the composer of the chat the user just left.
  useEffect(() => {
    setStaged([])
    setError(null)
  }, [conversationId])

  // Poll only while something staged is still being indexed. Scoped to the
  // staged ids so a document sent three turns ago is never re-fetched.
  const pendingIds = staged.filter((a) => a.status === 'pending').map((a) => a.id)
  const hasPending = pendingIds.length > 0

  useEffect(() => {
    if (!conversationId || !hasPending) return

    const tick = async () => {
      try {
        const records = await listAttachments(conversationId)
        if (activeConversationRef.current !== conversationId) return
        const byId = new Map(records.map((r) => [r.id, r]))
        // Refresh only the rows still staged; a record that vanished
        // server-side is dropped rather than left spinning forever.
        setStaged((current) =>
          current.map((a) => byId.get(a.id) ?? a).filter((a) => byId.has(a.id)),
        )
      } catch {
        // A failed status poll is not worth surfacing - the files are already
        // uploaded and the next tick usually succeeds.
      }
    }

    const timer = setInterval(() => void tick(), POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [conversationId, hasPending])

  const upload = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      setUploading(true)
      setError(null)

      try {
        // Attaching to a not-yet-created chat creates it first, so the files
        // have somewhere to belong.
        let targetId = conversationId
        if (!targetId) {
          const conversation = await createConversation()
          targetId = conversation.id
          activeConversationRef.current = targetId
          onConversationCreated?.(targetId)
        }

        const result = await uploadAttachments(targetId, files)

        if (activeConversationRef.current === targetId) {
          // Merge rather than replace: uploading a second file while the first
          // is still indexing must not drop the first.
          setStaged((current) => {
            const byId = new Map(current.map((a) => [a.id, a]))
            for (const record of result.attachments) byId.set(record.id, record)
            return [...byId.values()]
          })
        }

        if (result.rejected.length > 0) {
          setError(
            result.rejected.map((entry) => `${entry.name}: ${entry.reason}`).join('\n'),
          )
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Upload failed')
      } finally {
        setUploading(false)
      }
    },
    [conversationId, onConversationCreated],
  )

  const remove = useCallback((attachmentId: string) => {
    // Optimistic, and a real server-side delete: a staged file was never sent
    // with a message, so nothing in the transcript refers to it.
    setStaged((current) => current.filter((a) => a.id !== attachmentId))
    void deleteAttachmentRequest(attachmentId).catch(() => {})
  }, [])

  // Empties the composer without deleting anything: the files are now part of
  // the sent turn, and the chat reaches them through retrieval from here on.
  const clearStaged = useCallback(() => setStaged([]), [])

  const clearError = useCallback(() => setError(null), [])

  return { staged, uploading, error, upload, remove, clearStaged, clearError }
}
