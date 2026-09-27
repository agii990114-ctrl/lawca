import { useState } from 'react'
import { updateDeadlineStatus, updateEvent, type CalendarItem } from '../api'
import { useUser } from '../auth/UserContext'

const SOURCE_LABELS = { deadline: '기한', hearing: '기일', manual: '일정' }

function statusLabel(item: CalendarItem) {
  if (item.status === 'tentative') return '미확정'
  if (item.status === 'done') return '완료'
  return '확정'
}

// 선택한 기한·일정의 자세한 내용과 할 수 있는 일(권한에 따라 다름).
export default function ItemDetail({
  item,
  onChanged,
  onEdit,
  onOpenFile,
}: {
  item: CalendarItem
  onChanged: () => void
  onEdit: (item: CalendarItem) => void
  onOpenFile: (fileId: string, name: string) => void
}) {
  const isLawyer = useUser().role === 'lawyer'
  const [error, setError] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const isDeadline = item.source === 'deadline'
  return (
    <div className={`item-detail ${item.source} ${item.status}`}>
      <div className="item-detail-head">
        <span className={`source-tag ${item.source}`}>{SOURCE_LABELS[item.source]}</span>
        <span className={`status-tag ${item.status}`}>{statusLabel(item)}</span>
        {item.visibility === 'private' && <span className="status-tag">나만 보기</span>}
      </div>
      <h3>
        {item.time && <span className="item-time">{item.time} </span>}
        {item.title}
      </h3>
      {item.case_number && (
        <p className="muted">
          {item.case_number} · {[item.court, item.case_name].filter(Boolean).join(' · ')}
        </p>
      )}
      {item.details.length > 0 && (
        <ul className="item-lines">
          {item.details.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {item.memo && <p className="item-memo">{item.memo}</p>}
      <p className="muted">
        {item.status === 'tentative'
          ? `올린 사람: ${item.created_by}`
          : `확정: ${item.confirmed_by ?? item.created_by}`}
      </p>
      {error && <p className="issue error">{error}</p>}

      <div className="item-actions">
        {item.file_id && (
          <button type="button" className="secondary small" onClick={() => onOpenFile(item.file_id!, item.filename ?? '문서')}>
            문서 보기
          </button>
        )}
        {item.status === 'tentative' && (
          <button type="button" className="primary small" onClick={() => run(() => updateEvent(item.id, { status: 'confirmed' }))}>
            원문과 대조했음 · 확정
          </button>
        )}
        {isDeadline && item.status === 'confirmed' && (
          <button type="button" className="secondary small" onClick={() => run(() => updateDeadlineStatus(item.id, 'done'))}>
            완료
          </button>
        )}
        {isDeadline && isLawyer && (
          <button
            type="button"
            className="link-button"
            onClick={() => run(() => updateDeadlineStatus(item.id, item.status === 'done' ? 'confirmed' : 'cancelled'))}
          >
            {item.status === 'done' ? '되돌리기' : '기한 취소'}
          </button>
        )}
        {!isDeadline && item.can_edit && (
          <>
            <button type="button" className="secondary small" onClick={() => onEdit(item)}>
              수정
            </button>
            <button
              type="button"
              className="link-button danger"
              onClick={() => window.confirm('이 일정을 취소할까요?') && run(() => updateEvent(item.id, { status: 'cancelled' }))}
            >
              일정 취소
            </button>
          </>
        )}
      </div>
    </div>
  )
}
