/** Message list with a streaming cursor and sticky autoscroll. */
import { useEffect, useMemo, useRef } from 'react'

import { attachmentFileUrl } from '../api/client'
import type { ChartRunState } from '../hooks/useChartRunner'
import type { DisplayMessage } from '../hooks/useChat'
import { greetingFor } from '../utils/greeting'
import { MarkdownMessage } from './MarkdownMessage'
import { SourceList } from './SourceList'

interface ChatViewProps {
  messages: DisplayMessage[]
  streaming: boolean
  modelName: string
  /** False while the model is still connecting - shows a plain label instead
   * of the full greeting, since claiming to be "ready" would be misleading. */
  modelReady: boolean
  /** Lets python blocks in assistant replies offer a Run button. */
  chartRunner?: {
    stateFor: (code: string) => ChartRunState
    run: (code: string) => void
  }
}

export function ChatView({
  messages,
  streaming,
  modelName,
  modelReady,
  chartRunner,
}: ChatViewProps) {
  // Memoized on the model name (not re-rolled on every render) so it doesn't
  // change out from under the user while they're reading it.
  const greeting = useMemo(() => greetingFor(modelName), [modelName])
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedToBottom = useRef(true)

  // Only autoscroll while the user is already at the bottom, so scrolling up to
  // read earlier messages is not fought by incoming tokens.
  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    pinnedToBottom.current = distanceFromBottom < 80
  }

  useEffect(() => {
    if (!pinnedToBottom.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="chat-scroll" ref={scrollRef} onScroll={handleScroll}>
        <div className="chat-empty">
          <strong>{modelName}</strong>
          {modelReady ? greeting : 'Ask anything. Runs entirely on this machine.'}
        </div>
      </div>
    )
  }

  // A per-turn model label only earns its place when the model actually
  // changed partway through - otherwise every assistant bubble would repeat
  // the same name the header already shows.
  let previousAssistantModel: string | null | undefined = undefined

  return (
    <div className="chat-scroll" ref={scrollRef} onScroll={handleScroll}>
      {messages.map((message, index) => {
        const isLast = index === messages.length - 1
        const showCursor = streaming && isLast && message.role === 'assistant'

        let showModelLabel = false
        if (message.role === 'assistant') {
          showModelLabel =
            previousAssistantModel !== undefined &&
            message.modelUsedForThisTurn !== previousAssistantModel
          previousAssistantModel = message.modelUsedForThisTurn
        }

        return (
          <div className={`message ${message.role}`} key={index}>
            <div className="message-avatar">
              {message.role === 'user' ? 'You' : 'AI'}
            </div>
            <div className="message-content-wrap">
              {showModelLabel && message.modelUsedForThisTurn && (
                <div className="message-model-label">
                  {message.modelUsedForThisTurn}
                </div>
              )}
              {/* Files shown above the text they were sent with, mirroring how
                  the composer stacked them when the user attached them. */}
              {message.attachments && message.attachments.length > 0 && (
                <div className="message-attachments">
                  {message.attachments.map((attachment) =>
                    attachment.kind === 'image' ? (
                      <img
                        key={attachment.id}
                        className="message-attachment-image"
                        src={attachmentFileUrl(attachment.id)}
                        alt={attachment.filename}
                        title={attachment.filename}
                      />
                    ) : (
                      <span key={attachment.id} className="message-attachment-file">
                        {attachment.filename}
                      </span>
                    ),
                  )}
                </div>
              )}
              {/* Above the bubble: citations arrive before the first token, so
                  this fills the pause while the model reads the pages. */}
              {message.citations && message.citations.length > 0 && (
                <SourceList citations={message.citations} streaming={showCursor} />
              )}
              <div className="message-bubble">
                {message.role === 'assistant' ? (
                  <MarkdownMessage content={message.content} chartRunner={chartRunner} />
                ) : (
                  message.content
                )}
                {showCursor && <span className="cursor" />}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
