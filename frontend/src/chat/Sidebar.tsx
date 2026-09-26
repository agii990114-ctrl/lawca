import type { ConversationSummary } from '../api'

export default function Sidebar({
  conversations,
  activeId,
  loadError,
  onSelect,
  onNew,
}: {
  conversations: ConversationSummary[]
  activeId: string | null
  loadError: string | null
  onSelect: (id: string) => void
  onNew: () => void
}) {
  return (
    <nav className="sidebar" aria-label="대화 목록">
      <div className="brand">lawca</div>
      <button type="button" className="new-chat" onClick={onNew}>
        + 새 대화
      </button>
      <div className="sidebar-label">대화</div>
      {loadError && <p className="issue error">{loadError}</p>}
      <ul>
        {conversations.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              className={c.id === activeId ? 'active' : undefined}
              onClick={() => onSelect(c.id)}
              title={c.title}
            >
              {c.title}
            </button>
          </li>
        ))}
      </ul>
      <p className="sidebar-note">대화와 첨부 파일은 자동으로 저장됩니다.</p>
    </nav>
  )
}
