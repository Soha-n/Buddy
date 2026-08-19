/**
 * Auto-growing composer. Enter sends, Shift+Enter adds a newline.
 *
 * Also the attachment surface: a paperclip, drag-and-drop over the whole
 * composer, and paste. Paste matters more than it looks - screenshotting a chart
 * and hitting Ctrl+V is the fastest way most people share an image, and a
 * pasted image arrives with no filename, so one is synthesized.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

interface ChatInputProps {
  streaming: boolean
  disabled?: boolean
  placeholder?: string
  /** Blocks sending without disabling the textarea, so the user can keep typing
   *  while a warning (e.g. an image on a text-only model) is unresolved. */
  sendBlocked?: boolean
  onSend: (content: string) => void
  onStop: () => void
  onAttach?: (files: File[]) => void
  /** Web search state. Owned by the parent so it survives this component's
   *  remounts and can be read at send time. */
  webSearch?: boolean
  onToggleWebSearch?: () => void
  /** False when no provider is set up. The toggle stays *clickable* - a control
   *  the user cannot even flip is a dead end - and the unmet requirement is
   *  surfaced as a warning above the composer instead. */
  webSearchAvailable?: boolean
  webSearchDetail?: string | null
}

const MAX_HEIGHT_PX = 176

/** Extensions the backend accepts, so the picker doesn't offer rejects. */
const ACCEPTED_TYPES =
  '.pdf,.docx,.docm,.csv,.tsv,.xlsx,.xlsm,.xls,.txt,.md,.markdown,.rst,.log,' +
  '.json,.xml,.yaml,.yml,.png,.jpg,.jpeg,.gif,.webp,.bmp'

export function ChatInput({
  streaming,
  disabled = false,
  placeholder,
  sendBlocked = false,
  onSend,
  onStop,
  onAttach,
  webSearch = false,
  onToggleWebSearch,
  webSearchAvailable = true,
  webSearchDetail,
}: ChatInputProps) {
  const [value, setValue] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isDisabled = disabled && !streaming

  // Grow with content up to a cap, then scroll internally.
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`
  }, [value])

  // Return focus to the composer once it's usable again.
  useEffect(() => {
    if (!streaming && !isDisabled) textareaRef.current?.focus()
  }, [streaming, isDisabled])

  const submit = useCallback(() => {
    const trimmed = value.trim()
    if (!trimmed || streaming || isDisabled || sendBlocked) return
    onSend(trimmed)
    setValue('')
  }, [isDisabled, onSend, sendBlocked, streaming, value])

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || !onAttach) return
      const files = Array.from(fileList)
      if (files.length > 0) onAttach(files)
    },
    [onAttach],
  )

  const handlePaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!onAttach) return
    const files = Array.from(event.clipboardData.files)
    if (files.length === 0) return
    event.preventDefault()
    // A pasted screenshot has an empty name; without one the backend cannot
    // detect its kind, so it is given a real extension from its MIME type.
    onAttach(
      files.map((file) => {
        if (file.name) return file
        const extension = file.type.split('/')[1] || 'png'
        return new File([file], `pasted-image.${extension}`, { type: file.type })
      }),
    )
  }

  const effectivePlaceholder = streaming
    ? 'Waiting for the response…'
    : (placeholder ??
      (webSearch
        ? 'Ask anything — Buddy will search the web…'
        : 'Send a message, or drop in a file…'))

  return (
    <form
      className={`chat-form${dragActive ? ' drag-active' : ''}`}
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
      onDragOver={(event) => {
        if (!onAttach) return
        event.preventDefault()
        setDragActive(true)
      }}
      onDragLeave={() => setDragActive(false)}
      onDrop={(event) => {
        if (!onAttach) return
        event.preventDefault()
        setDragActive(false)
        handleFiles(event.dataTransfer.files)
      }}
    >
      {/* One rounded bar holding everything, rather than a row of separate
          controls: the whole strip is the drop target, and the focus ring reads
          as a single field instead of lighting up only the middle of it. */}
      <div className="composer-bar">
        {onAttach && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPTED_TYPES}
              className="chat-file-input"
              onChange={(event) => {
                handleFiles(event.target.files)
                // Reset so re-picking the same file fires onChange again.
                event.target.value = ''
              }}
            />
            <button
              type="button"
              className="composer-plus"
              onClick={() => fileInputRef.current?.click()}
              disabled={streaming}
              aria-label="Add files"
              title="Add a PDF, Word, Excel, CSV, text file or image — or just drop one here"
            >
              <span className="plus-icon" aria-hidden="true" />
            </button>
          </>
        )}

        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={effectivePlaceholder}
          disabled={streaming || isDisabled}
          rows={1}
        />

        <div className="composer-actions">
          {onToggleWebSearch && (
            <button
              type="button"
              className={`web-toggle${webSearch ? ' active' : ''}${
                webSearch && !webSearchAvailable ? ' needs-setup' : ''
              }`}
              onClick={onToggleWebSearch}
              // Only blocked while a reply is streaming, when changing it
              // mid-answer would be meaningless. An unconfigured provider does
              // NOT disable it: the user still needs to see and set the state.
              disabled={streaming}
              aria-pressed={webSearch}
              title={
                webSearch
                  ? `Web search on${webSearchDetail ? ` — ${webSearchDetail}` : ''}. Click to turn off.`
                  : `Web search off. Click to let Buddy look things up${webSearchDetail ? ` — ${webSearchDetail}` : ''}.`
              }
            >
              <span className="web-icon" aria-hidden="true" />
              <span className="web-toggle-label">Web</span>
            </button>
          )}

          {streaming ? (
            <button
              type="button"
              className="send-button"
              onClick={onStop}
              aria-label="Stop generating"
            >
              <span className="stop-icon" />
            </button>
          ) : (
            <button
              type="submit"
              className="send-button primary"
              disabled={!value.trim() || isDisabled || sendBlocked}
              aria-label="Send message"
            >
              <span className="send-icon" />
            </button>
          )}
        </div>
      </div>
    </form>
  )
}
