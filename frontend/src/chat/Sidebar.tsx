import type { Conversation } from './types'

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
}: {
  conversations: Conversation[]
  activeId: string
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
      <ul>
        {conversations
          .filter((c) => c.messages.length > 0)
          .map((c) => (
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
      <p className="sidebar-note">대화는 아직 저장되지 않습니다. 새로고침하면 사라집니다.</p>
    </nav>
  )
}
