/**
 * Chat screen: transcript, composer, staged attachments and model switcher.
 *
 * The image rule lives here because it is cross-cutting: it depends on the
 * staged files (is one of them an image?), the selected model (can it see?), and
 * the switcher (what can the user change to?). The same rule is enforced in
 * /api/chat, so this layer exists to tell the user before they send rather than
 * after, and to put the fix one click away.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import { useAttachments } from '../hooks/useAttachments'
import { useChartRunner } from '../hooks/useChartRunner'
import { useChat } from '../hooks/useChat'
import type { PreloadState } from '../hooks/useModelPreload'
import { useVisionCheck } from '../hooks/useVisionCheck'
import { useWebSearchStatus } from '../hooks/useWebSearchStatus'
import type { ConversationDetail, InstalledModel } from '../types/api'
import { AttachmentChips } from './AttachmentChips'
import { ChatInput } from './ChatInput'
import { ChatView } from './ChatView'
import { ModelSwitcher } from './ModelSwitcher'
import { VisionWarning } from './VisionWarning'

interface ChatScreenProps {
  conversationId: string | null
  conversation: ConversationDetail | null
  /** Fully controlled by the parent, so both the in-chat switcher and Manage
   * Models' "Use this model" can change it and stay in sync with each other. */
  currentModel: string
  onModelChange: (model: string) => void
  installed: InstalledModel[]
  preloadState: PreloadState
  preloadError: string | null
  preloadTargetModel: string | null
  ensureModelLoaded: (model: string) => void
  onConversationCreated: (id: string) => void
  onOpenManageModels: () => void
}

export function ChatScreen({
  conversationId,
  conversation,
  currentModel,
  onModelChange,
  installed,
  preloadState,
  preloadError,
  preloadTargetModel,
  ensureModelLoaded,
  onConversationCreated,
  onOpenManageModels,
}: ChatScreenProps) {
  // ensureModelLoaded is a no-op if this model is already loaded or loading,
  // so navigating between chats that use the same model never reconnects -
  // the connection is kept for the whole app session, only switching when the
  // user actually picks a different model.
  useEffect(() => {
    ensureModelLoaded(currentModel)
  }, [currentModel, ensureModelLoaded])

  const { messages, streaming, error, lastStats, send, stop } = useChat({
    conversationId,
    initialMessages: conversation?.messages,
    onConversationCreated,
  })

  const attachments = useAttachments({ conversationId, onConversationCreated })
  const chartRunner = useChartRunner(conversationId)
  const vision = useVisionCheck(currentModel)
  const webSearchStatus = useWebSearchStatus()

  // Off by default, every session. Deliberately not persisted: sending the
  // user's question to a search engine is an outbound network call, so it
  // should be a choice they just made rather than one they made once and forgot.
  const [webSearch, setWebSearch] = useState(false)

  const stagedImages = useMemo(
    () => attachments.staged.filter((a) => a.kind === 'image'),
    [attachments.staged],
  )

  // Only gate once the capability answer is actually known: `null` means the
  // check is still in flight, and treating that as "cannot see" would flash a
  // warning at a model that turns out to be fine.
  const imageOnTextModel = stagedImages.length > 0 && vision.supportsVision === false

  const preloadTargetsThisModel = preloadTargetModel === currentModel
  const isCurrentModelLoading = preloadState === 'loading' && preloadTargetsThisModel
  const currentModelFailed = preloadState === 'error' && preloadTargetsThisModel
  const composerDisabled = streaming || isCurrentModelLoading

  // Files still indexing are not searchable yet, so sending is held until they
  // settle - otherwise the first question about a document is answered from
  // nothing.
  const indexing = attachments.staged.some((a) => a.status === 'pending')

  const handleSend = useCallback(
    (content: string) => {
      const ready = attachments.staged.filter((a) => a.status === 'ready')
      send(content, currentModel, ready, webSearch)
      // The composer empties here. These files now belong to the turn just
      // sent; the chat reaches their contents through retrieval from now on,
      // so they must not linger and be re-attached to the next message.
      attachments.clearStaged()
    },
    [attachments, currentModel, send, webSearch],
  )

  const handleRemoveStagedImages = useCallback(() => {
    for (const image of stagedImages) attachments.remove(image.id)
  }, [attachments, stagedImages])

  return (
    <div className="chat">
      <ChatView
        messages={messages}
        streaming={streaming}
        modelName={currentModel}
        modelReady={!isCurrentModelLoading && !currentModelFailed}
        chartRunner={chartRunner}
      />

      {error && (
        <div className="error-box chat-inline-error">
          <strong>Something went wrong</strong>
          {error}
        </div>
      )}

      {currentModelFailed && preloadError && (
        <div className="error-box chat-inline-error">
          <strong>Couldn't load {currentModel}</strong>
          {preloadError}
        </div>
      )}

      {attachments.error && (
        <div className="error-box chat-inline-error">
          <strong>Some files weren't added</strong>
          {attachments.error}
          <button
            type="button"
            className="inline-dismiss"
            onClick={attachments.clearError}
          >
            Dismiss
          </button>
        </div>
      )}

      {imageOnTextModel && (
        <VisionWarning
          currentModel={currentModel}
          installedVisionModels={vision.installedVisionModels}
          onSwitchModel={onModelChange}
          onOpenManageModels={onOpenManageModels}
          onRemoveImages={handleRemoveStagedImages}
        />
      )}

      <AttachmentChips
        attachments={attachments.staged}
        uploading={attachments.uploading}
        onRemove={attachments.remove}
      />

      <ChatInput
        streaming={streaming}
        disabled={composerDisabled}
        sendBlocked={imageOnTextModel || indexing || attachments.uploading}
        placeholder={
          isCurrentModelLoading
            ? 'Connecting to model…'
            : indexing
              ? 'Reading your files…'
              : undefined
        }
        onSend={handleSend}
        onStop={stop}
        onAttach={(files) => void attachments.upload(files)}
        webSearch={webSearch}
        onToggleWebSearch={() => setWebSearch((v) => !v)}
        webSearchAvailable={webSearchStatus.available}
        webSearchDetail={webSearchStatus.detail}
      />

      <div className="chat-footer">
        <ModelSwitcher
          currentModel={currentModel}
          installed={installed}
          loading={isCurrentModelLoading}
          tokensPerSec={lastStats?.tokensPerSec ?? null}
          onChange={onModelChange}
          onBrowseMore={onOpenManageModels}
          disabled={streaming}
        />
      </div>
    </div>
  )
}
