/** Persistent left shell: branding, new chat, history, manage-models entry. */
import type { ConversationSummary } from '../types/api'
import { ConversationList } from './ConversationList'

interface SidebarProps {
  conversations: ConversationSummary[]
  activeId: string | null
  onNewChat: () => void
  onOpenConversation: (id: string) => void
  onRenameConversation: (id: string, title: string) => void
  onDeleteConversation: (id: string) => void
  onOpenManageModels: () => void
  managingModels: boolean
}

export function Sidebar({
  conversations,
  activeId,
  onNewChat,
  onOpenConversation,
  onRenameConversation,
  onDeleteConversation,
  onOpenManageModels,
  managingModels,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <img src="/logo.svg" alt="" className="sidebar-mark" aria-hidden="true" />
        <div className="sidebar-brand">
          <span className="sidebar-logo">Buddy</span>
          <span className="sidebar-logo-sub">private</span>
        </div>
      </div>

      <button type="button" className="new-chat-button" onClick={onNewChat}>
        <span className="new-chat-icon">+</span>
        New chat
      </button>

      <div className="sidebar-history">
        <ConversationList
          conversations={conversations}
          activeId={activeId}
          onOpen={onOpenConversation}
          onRename={onRenameConversation}
          onDelete={onDeleteConversation}
        />
      </div>

      <button
        type="button"
        className={`manage-models-button${managingModels ? ' active' : ''}`}
        onClick={onOpenManageModels}
      >
        <span className="manage-models-icon">⚙</span>
        Manage models
      </button>
    </aside>
  )
}
