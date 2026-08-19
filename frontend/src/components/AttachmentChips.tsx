/** Attached-file chips above the composer, with indexing state and removal. */
import type { AttachmentRecord, AttachmentKind } from '../types/api'
import { attachmentFileUrl } from '../api/client'

interface AttachmentChipsProps {
  attachments: AttachmentRecord[]
  uploading: boolean
  onRemove: (id: string) => void
}

const KIND_LABELS: Record<AttachmentKind, string> = {
  pdf: 'PDF',
  docx: 'DOC',
  table: 'DATA',
  text: 'TXT',
  image: 'IMG',
  unsupported: '?',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Short, plain-language status. Empty for a ready file - no news is good news. */
function statusLabel(attachment: AttachmentRecord): string {
  if (attachment.status === 'pending') return 'Indexing…'
  if (attachment.status === 'error') return 'Failed'
  if (attachment.kind === 'image') {
    return attachment.has_description ? 'Readable by any model' : 'Image'
  }
  return attachment.chunk_count > 0 ? `${attachment.chunk_count} sections` : 'Ready'
}

export function AttachmentChips({
  attachments,
  uploading,
  onRemove,
}: AttachmentChipsProps) {
  if (attachments.length === 0 && !uploading) return null

  return (
    <div className="attachment-chips">
      {attachments.map((attachment) => (
        <div
          key={attachment.id}
          className={`attachment-chip ${attachment.status}`}
          title={attachment.error ?? attachment.filename}
        >
          {attachment.kind === 'image' ? (
            <img
              className="attachment-chip-thumb"
              src={attachmentFileUrl(attachment.id)}
              alt=""
            />
          ) : (
            <span className="attachment-chip-kind">{KIND_LABELS[attachment.kind]}</span>
          )}

          <span className="attachment-chip-body">
            <span className="attachment-chip-name">{attachment.filename}</span>
            <span className="attachment-chip-meta">
              {formatSize(attachment.size_bytes)} · {statusLabel(attachment)}
            </span>
          </span>

          {attachment.status === 'pending' && (
            <span className="attachment-chip-spinner" aria-hidden="true" />
          )}

          <button
            type="button"
            className="attachment-chip-remove"
            onClick={() => onRemove(attachment.id)}
            aria-label={`Remove ${attachment.filename}`}
          >
            ×
          </button>
        </div>
      ))}

      {uploading && (
        <div className="attachment-chip pending">
          <span className="attachment-chip-spinner" aria-hidden="true" />
          <span className="attachment-chip-body">
            <span className="attachment-chip-name">Uploading…</span>
          </span>
        </div>
      )}
    </div>
  )
}
