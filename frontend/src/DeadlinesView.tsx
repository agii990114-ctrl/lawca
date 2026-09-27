import { useCallback, useEffect, useState, type MouseEvent } from 'react'
import {
  DEADLINE_EXPORT,
  deleteDeadline,
  dismissPending,
  getPending,
  listDeadlines,
  updateDeadlineStatus,
  updateEvent,
  type CalendarItem,
  type DeadlineRecord,
  type DeadlineStatus,
  type PendingDocument,
} from './api'
import { useUser } from './auth/UserContext'
import { toIso } from './calendar/dates'
import DocumentPopup, { type PopupTarget } from './deadlines/DocumentPopup'
import { dDay, daysUntil, formatDate } from './format'

type Tab = 'pending' | DeadlineStatus

const TABS: { key: Tab; label: string }[] = [
  { key: 'pending', label: '대기' },
  { key: 'confirmed', label: '진행 중' },
  { key: 'done', label: '완료' },
  { key: 'cancelled', label: '취소' },
]

function formatDateTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${formatDate(toIso(d))} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// 행을 누르면 팝업이 열리므로, 행 안의 버튼은 누름이 행으로 번지지 않게 한다.
const stop = (fn: () => void) => (e: MouseEvent) => {
  e.stopPropagation()
  fn()
}

export default function DeadlinesView() {
  const isLawyer = useUser().role === 'lawyer'
  const [tab, setTab] = useState<Tab>('confirmed')
  const [items, setItems] = useState<DeadlineRecord[] | null>(null)
  const [pending, setPending] = useState<{ documents: PendingDocument[]; hearings: CalendarItem[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [popup, setPopup] = useState<PopupTarget | null>(null)

  const load = useCallback(() => {
    getPending()
      .then(setPending)
      .catch((e: Error) => setError(e.message))
    if (tab === 'pending') return
    listDeadlines({ status: [tab] })
      .then((list) => {
        // 완료·취소는 최근에 바꾼 것부터
        if (tab !== 'confirmed') list.sort((a, b) => (b.status_changed_at ?? '').localeCompare(a.status_changed_at ?? ''))
        setItems(list)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [tab])

  useEffect(load, [load])

  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const pendingCount = pending ? pending.documents.length + pending.hearings.length : 0
  const rows = tab === 'pending' ? null : items

  return (
    <div className="deadlines-view">
      <header className="view-header">
        <div>
          <h1>기한 목록</h1>
          <p className="muted">
            대기: 송달일을 넣어 기한을 확정하거나 기일을 확정할 문서. 행을 누르면 문서 상세를 봅니다(대기·진행 중은 수정 가능).
          </p>
        </div>
        <div className="export">
          <a className="button-like" href={DEADLINE_EXPORT.ics} download>
            캘린더 파일로 내보내기
          </a>
          <a className="button-like" href={DEADLINE_EXPORT.csv} download>
            엑셀로 내보내기
          </a>
        </div>
      </header>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={t.key === tab}
            className={t.key === tab ? 'active' : undefined}
            onClick={() => {
              setItems(null)
              setTab(t.key)
            }}
          >
            {t.label}
            {t.key === 'pending' && pendingCount > 0 && <span className="tab-count">{pendingCount}</span>}
          </button>
        ))}
      </div>

      {error && <p className="issue error">{error}</p>}

      {tab === 'pending' && pending && (
        pendingCount === 0 ? (
          <p className="muted empty-note">대기 중인 문서가 없습니다.</p>
        ) : (
          <div className="table-scroll">
            <table className="deadline-table clickable">
              <thead>
                <tr>
                  <th>할 일</th>
                  <th>문서</th>
                  <th>사건</th>
                  <th>받은 날</th>
                  <th aria-label="처리" />
                </tr>
              </thead>
              <tbody>
                {pending.hearings.map((h) => (
                  <tr key={h.id} onClick={() => setPopup({ kind: 'hearing', documentId: h.document_id, hearing: h })}>
                    <td>
                      <span className="todo hearing">기일 확정</span>
                      <div>
                        <strong>{formatDate(h.day)}</strong> {h.time}
                      </div>
                    </td>
                    <td>
                      {h.document_type ?? '기일'}
                      <div className="muted">{h.title.replace(/^\[기일\] /, '').replace(/ · \S+$/, '')}</div>
                    </td>
                    <td>
                      {h.case_number ?? '사건번호 없음'}
                      <div className="muted">{[h.court, h.case_name].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td className="muted">{h.created_by}</td>
                    <td className="actions">
                      <button type="button" className="secondary small" onClick={stop(() => run(() => updateEvent(h.id, { status: 'confirmed' })))}>
                        확정
                      </button>
                    </td>
                  </tr>
                ))}
                {pending.documents.map((d) => (
                  <tr key={d.document_id} onClick={() => setPopup({ kind: 'document', documentId: d.document_id })}>
                    <td>
                      <span className="todo">송달일 입력</span>
                      <div className="muted">{d.suggestions.map((s) => s.label).join(', ')}</div>
                    </td>
                    <td>
                      {d.document_type}
                      <div className="muted">{d.issued_date ? `${formatDate(d.issued_date)} 발령` : d.filename}</div>
                    </td>
                    <td>
                      {d.case_number ?? '사건번호 없음'}
                      <div className="muted">{d.court}</div>
                    </td>
                    <td className="muted">{formatDateTime(d.created_at)}</td>
                    <td className="actions">
                      <button
                        type="button"
                        className="link-button"
                        onClick={stop(
                          () => window.confirm('이 문서는 기한을 잡지 않고 대기 목록에서 뺄까요?') && run(() => dismissPending(d.document_id)),
                        )}
                      >
                        기한 없음
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {rows && rows.length === 0 && <p className="muted empty-note">해당하는 기한이 없습니다.</p>}

      {rows && rows.length > 0 && (
        <div className="table-scroll">
          <table className="deadline-table clickable">
            <thead>
              <tr>
                <th>만료일</th>
                <th>기한</th>
                <th>사건</th>
                <th>송달일</th>
                <th>{tab === 'confirmed' ? '확정' : tab === 'done' ? '완료 시각' : '취소 시각'}</th>
                <th aria-label="처리" />
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => {
                const days = daysUntil(d.deadline)
                const urgent = d.status === 'confirmed' && days <= 3
                return (
                  <tr
                    key={d.id}
                    className={urgent ? 'urgent-row' : undefined}
                    onClick={() => setPopup({ kind: 'deadline', documentId: d.document_id, deadlineId: d.id })}
                  >
                    <td>
                      <span className={`dday${urgent ? ' urgent' : ''}`}>{d.status === 'confirmed' ? dDay(days) : ''}</span>
                      <strong>{formatDate(d.deadline)}</strong>
                    </td>
                    <td>
                      {d.label}
                      <div className="muted">
                        {d.period.label} · {d.document_type}
                      </div>
                    </td>
                    <td>
                      {d.case_number ?? '사건번호 없음'}
                      <div className="muted">{[d.court, d.case_name].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td>
                      {formatDate(d.event_date)}
                      <div className="muted">{d.service_label}</div>
                    </td>
                    <td className="muted">
                      {tab === 'confirmed' ? d.confirmed_by : formatDateTime(d.status_changed_at)}
                    </td>
                    <td className="actions">
                      {d.status === 'confirmed' && (
                        <>
                          <button type="button" className="secondary small" onClick={stop(() => run(() => updateDeadlineStatus(d.id, 'done')))}>
                            완료
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            onClick={stop(() => window.confirm(`'${d.label}'을(를) 취소할까요? 취소 탭에서 복원할 수 있습니다.`) && run(() => updateDeadlineStatus(d.id, 'cancelled')))}
                          >
                            취소
                          </button>
                        </>
                      )}
                      {d.status !== 'confirmed' && (
                        <button type="button" className="secondary small" onClick={stop(() => run(() => updateDeadlineStatus(d.id, 'confirmed')))}>
                          복원
                        </button>
                      )}
                      {d.status === 'cancelled' && isLawyer && (
                        <button
                          type="button"
                          className="link-button danger"
                          onClick={stop(() => window.confirm(`'${d.label}'을(를) 삭제할까요? 삭제하면 목록에서 사라지고 복원할 수 없습니다.`) && run(() => deleteDeadline(d.id)))}
                        >
                          삭제
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

      {popup && <DocumentPopup target={popup} onClose={() => setPopup(null)} onChanged={load} />}
    </div>
  )
}
