import type { ReactNode } from 'react'
import type { ConversationSummary } from '../api'

export default function Sidebar({
  conversations,
  activeId,
  loadError,
  view,
  open,
  onSelect,
  onNew,
  onShowView,
  footer,
}: {
  conversations: ConversationSummary[]
  activeId: string | null
  loadError: string | null
  view: 'chat' | 'deadlines' | 'calendar'
  open: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onShowView: (view: 'deadlines' | 'calendar') => void
  footer?: ReactNode
}) {
  return (
    <nav className={`sidebar${open ? ' open' : ''}`} aria-label="대화 목록">
      <div className="brand">lawca</div>
      <button type="button" className="new-chat" onClick={onNew}>
        + 새 대화
      </button>
      <button
        type="button"
        className={`nav-item${view === 'calendar' ? ' active' : ''}`}
        onClick={() => onShowView('calendar')}
      >
        캘린더
      </button>
      <button
        type="button"
        className={`nav-item${view === 'deadlines' ? ' active' : ''}`}
        onClick={() => onShowView('deadlines')}
      >
        기한 목록
      </button>
      <div className="sidebar-label">대화</div>
      {loadError && <p className="issue error">{loadError}</p>}
      <ul>
        {conversations.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              className={view === 'chat' && c.id === activeId ? 'active' : undefined}
              onClick={() => onSelect(c.id)}
              title={c.title}
            >
              {c.title}
            </button>
          </li>
        ))}
      </ul>
      <p className="sidebar-note">대화와 첨부 파일은 자동으로 저장됩니다. 대화는 본인에게만 보입니다.</p>
      {footer}
    </nav>
  )
}
