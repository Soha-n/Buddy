/** Groups conversations into Today/Yesterday/Previous 7 days/Older, ChatGPT-style. */
import { groupByRecency } from '../utils/groupByRecency'
import type { ConversationSummary } from '../types/api'
import { ConversationItem } from './ConversationItem'

interface ConversationListProps {
  conversations: ConversationSummary[]
  activeId: string | null
  onOpen: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
}

const SECTION_LABELS: { key: keyof ReturnType<typeof groupByRecency>; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: 'previous7Days', label: 'Previous 7 days' },
  { key: 'older', label: 'Older' },
]

export function ConversationList({
  conversations,
  activeId,
  onOpen,
  onRename,
  onDelete,
}: ConversationListProps) {
  const groups = groupByRecency(conversations)

  if (conversations.length === 0) {
    return <div className="conversation-list-empty">No conversations yet</div>
  }

  return (
    <div className="conversation-list">
      {SECTION_LABELS.map(({ key, label }) => {
        const items = groups[key]
        if (items.length === 0) return null
        return (
          <div className="conversation-section" key={key}>
            <div className="conversation-section-label">{label}</div>
            {items.map((conversation) => (
              <ConversationItem
                key={conversation.id}
                conversation={conversation}
                active={conversation.id === activeId}
                onOpen={onOpen}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </div>
        )
      })}
    </div>
  )
}
