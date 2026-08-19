/**
 * Web sources behind an answer, shown above the reply.
 *
 * Placed above rather than below because it arrives first: the backend emits
 * citations before the first token, so this panel is what fills the pause while
 * the model reads the pages. It doubles as an explanation of why this reply took
 * a few seconds longer than usual.
 *
 * Collapsed by default. Sources are for checking an answer, not for reading
 * alongside it, so they should not push the answer itself off screen.
 */
import { useState } from 'react'

import type { SearchCitation } from '../types/api'

interface SourceListProps {
  citations: SearchCitation[]
  /** Rendered while tokens are still arriving, to explain the wait. */
  streaming?: boolean
}

/** "coindesk.com" from a full URL - the part a reader actually recognizes. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function SourceList({ citations, streaming = false }: SourceListProps) {
  const [open, setOpen] = useState(false)

  if (citations.length === 0) return null

  const readCount = citations.filter((c) => c.fetched).length

  return (
    <div className="source-list">
      <button
        type="button"
        className="source-list-toggle"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="source-globe" aria-hidden="true" />
        <span>
          {streaming ? 'Reading' : 'Searched'} {citations.length} web{' '}
          {citations.length === 1 ? 'source' : 'sources'}
          {/* Which sources were actually read matters: a page that only
              contributed a search snippet supports a weaker claim than one
              that was fetched in full. */}
          {readCount > 0 && ` · ${readCount} read in full`}
        </span>
        <span className="source-list-caret">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <ol className="source-list-items">
          {citations.map((citation) => (
            <li key={citation.index}>
              <span className="source-index">[{citation.index}]</span>
              <a
                href={citation.url}
                target="_blank"
                rel="noopener noreferrer"
                title={citation.url}
              >
                {citation.title || hostOf(citation.url)}
              </a>
              <span className="source-host">{hostOf(citation.url)}</span>
              {citation.fetched && (
                <span className="source-read" title="Full page was read">
                  read
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
