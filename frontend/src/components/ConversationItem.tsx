/** One sidebar row: title, click-to-open, hover kebab menu for rename/delete. */
import { useEffect, useRef, useState } from 'react'

import type { ConversationSummary } from '../types/api'

interface ConversationItemProps {
  conversation: ConversationSummary
  active: boolean
  onOpen: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
}

export function ConversationItem({
  conversation,
  active,
  onOpen,
  onRename,
  onDelete,
}: ConversationItemProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draftTitle, setDraftTitle] = useState(conversation.title)
  const menuRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const handleClick = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [menuOpen])

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  const commitRename = () => {
    const trimmed = draftTitle.trim()
    if (trimmed && trimmed !== conversation.title) {
      onRename(conversation.id, trimmed)
    }
    setEditing(false)
  }

  if (editing) {
    return (
      <div className="conversation-item editing">
        <input
          ref={inputRef}
          value={draftTitle}
          onChange={(e) => setDraftTitle(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename()
            if (e.key === 'Escape') {
              setDraftTitle(conversation.title)
              setEditing(false)
            }
          }}
        />
      </div>
    )
  }

  return (
    <div className={`conversation-item${active ? ' active' : ''}`}>
      <button
        type="button"
        className="conversation-item-title"
        onClick={() => onOpen(conversation.id)}
        title={conversation.title}
      >
        {conversation.title}
      </button>

      <div className="conversation-item-menu" ref={menuRef}>
        <button
          type="button"
          className="conversation-item-kebab"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="Conversation options"
        >
          ⋯
        </button>
        {menuOpen && (
          <div className="conversation-menu-popover">
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false)
                setEditing(true)
              }}
            >
              Rename
            </button>
            <button
              type="button"
              className="danger"
              onClick={() => {
                setMenuOpen(false)
                onDelete(conversation.id)
              }}
            >
              Delete
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
