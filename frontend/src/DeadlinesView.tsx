import { useCallback, useEffect, useState } from 'react'
import { DEADLINE_EXPORT, listDeadlines, updateDeadlineStatus, type DeadlineRecord, type DeadlineStatus } from './api'
import { dDay, daysUntil, formatDate } from './format'

const FILTERS: { key: string; label: string; statuses: DeadlineStatus[] }[] = [
  { key: 'open', label: '진행 중', statuses: ['confirmed'] },
  { key: 'done', label: '완료', statuses: ['done'] },
  { key: 'cancelled', label: '취소', statuses: ['cancelled'] },
]

export default function DeadlinesView({ onOpenFile }: { onOpenFile: (fileId: string, name: string) => void }) {
  const [filter, setFilter] = useState(FILTERS[0])
  const [items, setItems] = useState<DeadlineRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listDeadlines({ status: filter.statuses })
      .then((list) => {
        setItems(list)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [filter])

  useEffect(load, [load])

  async function change(record: DeadlineRecord, status: DeadlineStatus) {
    try {
      await updateDeadlineStatus(record.id, status)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="deadlines-view">
      <header className="view-header">
        <div>
          <h1>기한</h1>
          <p className="muted">확정한 기한을 만료일 순으로 보여 줍니다. 만료일은 서버가 조문에 따라 계산한 값입니다.</p>
        </div>
        <div className="export">
          <a className="button-like" href={DEADLINE_EXPORT.ics} download>
            캘린더로 내보내기
          </a>
          <a className="button-like" href={DEADLINE_EXPORT.csv} download>
            엑셀로 내보내기
          </a>
        </div>
      </header>

      <div className="tabs" role="tablist">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            role="tab"
            aria-selected={f.key === filter.key}
            className={f.key === filter.key ? 'active' : undefined}
            onClick={() => setFilter(f)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && <p className="issue error">기한을 불러오지 못했습니다. {error}</p>}
      {items && items.length === 0 && <p className="muted empty-note">해당하는 기한이 없습니다.</p>}

      {items && items.length > 0 && (
        <div className="table-scroll">
          <table className="deadline-table">
            <thead>
              <tr>
                <th>만료일</th>
                <th>기한</th>
                <th>사건</th>
                <th>송달일</th>
                <th>문서</th>
                <th aria-label="처리" />
              </tr>
            </thead>
            <tbody>
              {items.map((d) => {
                const days = daysUntil(d.deadline)
                const urgent = d.status === 'confirmed' && days <= 3
                return (
                  <tr key={d.id} className={urgent ? 'urgent-row' : undefined}>
                    <td>
                      <span className={`dday${urgent ? ' urgent' : ''}`}>{d.status === 'confirmed' ? dDay(days) : ''}</span>
                      <strong>{formatDate(d.deadline)}</strong>
                    </td>
                    <td>
                      {d.label}
                      <div className="muted">
                        {d.period.label} · {d.basis[0]}
                      </div>
                      {d.warnings.length > 0 && <div className="warn-dot">주의 {d.warnings.length}건</div>}
                    </td>
                    <td>
                      {d.case_number ?? '사건번호 없음'}
                      <div className="muted">{[d.court, d.case_name].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td>
                      {formatDate(d.event_date)}
                      <div className="muted">{d.service_label}</div>
                    </td>
                    <td>
                      <button type="button" className="link-button" onClick={() => onOpenFile(d.file_id, d.filename)}>
                        {d.document_type}
                      </button>
                    </td>
                    <td className="actions">
                      {d.status === 'confirmed' && (
                        <>
                          <button type="button" className="secondary small" onClick={() => change(d, 'done')}>
                            완료
                          </button>
                          <button type="button" className="link-button" onClick={() => change(d, 'cancelled')}>
                            취소
                          </button>
                        </>
                      )}
                      {d.status !== 'confirmed' && (
                        <button type="button" className="link-button" onClick={() => change(d, 'confirmed')}>
                          되돌리기
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
