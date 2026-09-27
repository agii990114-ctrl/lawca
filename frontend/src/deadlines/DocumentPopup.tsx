import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  computeDeadline,
  correctDocumentCase,
  fileUrl,
  getDocument,
  SERVICE_LABELS,
  updateDeadlineTerms,
  updateEvent,
  type CalendarItem,
  type DeadlineRecord,
  type DeadlineResult,
  type DocumentDetail,
  type ServiceKind,
  type Unit,
} from '../api'
import DeadlinePanel from '../DeadlinePanel'
import { toIso } from '../calendar/dates'
import { formatDate } from '../format'

export type PopupTarget =
  | { kind: 'document'; documentId: string }
  | { kind: 'deadline'; documentId: string; deadlineId: string }
  | { kind: 'hearing'; documentId: string | null; hearing: CalendarItem }

function formatDateTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${formatDate(toIso(d))} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const STATUS_LABELS = { confirmed: '진행 중', done: '완료', cancelled: '취소' }

// 사건 정보(사건번호·법원·사건명) 고치기. 문서와 그 기한·기일이 고친 사건으로 옮겨 간다.
function CaseForm({ detail, onSaved, onCancel }: { detail: DocumentDetail; onSaved: (d: DocumentDetail) => void; onCancel: () => void }) {
  const [form, setForm] = useState({
    case_number: detail.case_number ?? '',
    court: detail.court ?? '',
    case_name: detail.case_name ?? '',
  })
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      onSaved(
        await correctDocumentCase(detail.document_id, {
          case_number: form.case_number,
          court: form.court || null,
          case_name: form.case_name || null,
        }),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <form className="popup-form" onSubmit={submit}>
      <label>
        사건번호
        <input value={form.case_number} onChange={(e) => setForm({ ...form, case_number: e.target.value })} required />
      </label>
      <label>
        법원
        <input value={form.court} onChange={(e) => setForm({ ...form, court: e.target.value })} />
      </label>
      <label>
        사건명
        <input value={form.case_name} onChange={(e) => setForm({ ...form, case_name: e.target.value })} />
      </label>
      {error && <p className="issue error">{error}</p>}
      <div className="modal-actions">
        <button type="button" className="secondary small" onClick={onCancel}>
          취소
        </button>
        <button type="submit" className="primary small">
          저장
        </button>
      </div>
    </form>
  )
}

// 진행 중인 기한 고치기. 새 기한을 만들지 않고 서버가 다시 계산한다.
function TermsForm({ deadline, onSaved, onCancel }: { deadline: DeadlineRecord; onSaved: () => void; onCancel: () => void }) {
  const designated = deadline.rule_id === null
  const [label, setLabel] = useState(deadline.label)
  const [eventDate, setEventDate] = useState(deadline.event_date)
  const [service, setService] = useState<ServiceKind>(deadline.service_kind)
  const [period, setPeriod] = useState<{ amount: number; unit: Unit }>({ amount: deadline.period.amount, unit: deadline.period.unit })
  const [preview, setPreview] = useState<DeadlineResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const body = {
    label,
    event_date: eventDate,
    service_kind: service,
    ...(designated ? { period: { ...period, label: '' } } : {}),
  }

  // 입력이 바뀌면 만료일을 미리 계산해 보여 준다(저장은 서버가 다시 계산한 값으로 한다).
  useEffect(() => {
    if (!eventDate) return
    const request = designated
      ? { event_date: eventDate, deemed_electronic_service: service === 'electronic_deemed', period: { ...period, label: '' } }
      : { event_date: eventDate, deemed_electronic_service: service === 'electronic_deemed', rule_id: deadline.rule_id! }
    computeDeadline(request)
      .then((r) => {
        setPreview(r)
        setError(null)
      })
      .catch((e: Error) => {
        setPreview(null)
        setError(e.message)
      })
  }, [eventDate, service, period, designated, deadline.rule_id])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await updateDeadlineTerms(deadline.id, body)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <form className="popup-form" onSubmit={submit}>
      <label>
        기한 이름
        <input value={label} onChange={(e) => setLabel(e.target.value)} required />
      </label>
      <div className="form-row">
        <label>
          송달일
          <input type="date" value={eventDate} onChange={(e) => setEventDate(e.target.value)} required />
        </label>
        <label>
          송달 유형
          <select value={service} onChange={(e) => setService(e.target.value as ServiceKind)}>
            {Object.entries(SERVICE_LABELS).map(([kind, text]) => (
              <option key={kind} value={kind}>
                {text}
              </option>
            ))}
          </select>
        </label>
      </div>
      {designated ? (
        <label>
          기간(문서가 정한 기간)
          <span className="inline-inputs">
            <input type="number" min={1} value={period.amount} onChange={(e) => setPeriod({ ...period, amount: Number(e.target.value) })} />
            <select value={period.unit} onChange={(e) => setPeriod({ ...period, unit: e.target.value as Unit })}>
              <option value="일">일</option>
              <option value="주">주</option>
              <option value="월">개월</option>
              <option value="년">년</option>
            </select>
          </span>
        </label>
      ) : (
        <p className="muted">법정 기간({deadline.period.label})은 바꿀 수 없습니다.</p>
      )}
      {preview && (
        <p className="preview-line">
          바뀐 만료일: <strong>{formatDate(preview.deadline)}</strong>
          {preview.deadline !== deadline.deadline && <span className="muted"> (지금 {formatDate(deadline.deadline)})</span>}
        </p>
      )}
      {error && <p className="issue error">{error}</p>}
      <div className="modal-actions">
        <button type="button" className="secondary small" onClick={onCancel}>
          취소
        </button>
        <button type="submit" className="primary small" disabled={!preview}>
          저장
        </button>
      </div>
    </form>
  )
}

function DeadlineInfo({ d }: { d: DeadlineRecord }) {
  return (
    <dl className="popup-dl">
      <dt>만료일</dt>
      <dd>
        <strong>{formatDate(d.deadline)}</strong> · {STATUS_LABELS[d.status]}
      </dd>
      <dt>송달일</dt>
      <dd>
        {formatDate(d.event_date)} ({d.service_label})
      </dd>
      <dt>기간</dt>
      <dd>
        {d.period.label} · 기산일 {formatDate(d.count_start)}
      </dd>
      {d.extended_over.length > 0 && (
        <>
          <dt>연장</dt>
          <dd>{d.extended_over.map((x) => `${formatDate(x.day)} ${x.reason}`).join(' → ')}</dd>
        </>
      )}
      <dt>근거</dt>
      <dd>{d.basis.join(', ')}</dd>
      <dt>확정</dt>
      <dd>
        {d.confirmed_by} · {formatDateTime(d.created_at)}
      </dd>
      {d.status !== 'confirmed' && (
        <>
          <dt>{d.status === 'done' ? '완료 시각' : '취소 시각'}</dt>
          <dd>{formatDateTime(d.status_changed_at)}</dd>
        </>
      )}
      {d.warnings.map((w) => (
        <dd key={w} className="issue warning">
          {w}
        </dd>
      ))}
    </dl>
  )
}

// 기일(미확정) 확정·고치기
function HearingSection({ hearing, onChanged }: { hearing: CalendarItem; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ day: hearing.day, time: hearing.time ?? '', location: hearing.location ?? '' })
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

  return (
    <section className="popup-section">
      <h3>기일</h3>
      {editing ? (
        <form
          className="popup-form"
          onSubmit={(e) => {
            e.preventDefault()
            run(() =>
              updateEvent(hearing.id, {
                day: form.day,
                location: form.location,
                ...(form.time ? { time: form.time } : { clear_time: true }),
              }),
            ).then(() => setEditing(false))
          }}
        >
          <div className="form-row">
            <label>
              날짜
              <input type="date" value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value })} required />
            </label>
            <label>
              시각
              <input type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} />
            </label>
          </div>
          <label>
            장소
            <input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} />
          </label>
          <div className="modal-actions">
            <button type="button" className="secondary small" onClick={() => setEditing(false)}>
              취소
            </button>
            <button type="submit" className="primary small">
              저장
            </button>
          </div>
        </form>
      ) : (
        <>
          <p>
            <strong>
              {formatDate(hearing.day)} {hearing.time}
            </strong>{' '}
            {hearing.title.replace(/^\[기일\] /, '')}
            {hearing.location && <span className="muted"> · {hearing.location}</span>}
          </p>
          <p className="muted">문서에서 읽은 기일입니다. 원문과 대조한 뒤 확정하면 캘린더에 올라갑니다.</p>
          <div className="item-actions">
            <button type="button" className="primary small" onClick={() => run(() => updateEvent(hearing.id, { status: 'confirmed' }))}>
              원문과 대조했음 · 확정
            </button>
            {hearing.can_edit && (
              <>
                <button type="button" className="secondary small" onClick={() => setEditing(true)}>
                  수정
                </button>
                <button
                  type="button"
                  className="link-button danger"
                  onClick={() => window.confirm('이 기일을 취소할까요?') && run(() => updateEvent(hearing.id, { status: 'cancelled' }))}
                >
                  기일 취소
                </button>
              </>
            )}
          </div>
        </>
      )}
      {error && <p className="issue error">{error}</p>}
    </section>
  )
}

// 기한 목록에서 행을 누르면 여는 문서 상세 팝업. 대기·진행 중에서는 고칠 수 있다.
export default function DocumentPopup({ target, onClose, onChanged }: { target: PopupTarget; onClose: () => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<'case' | 'terms' | null>(null)

  const load = useCallback(() => {
    if (!target.documentId) return
    getDocument(target.documentId)
      .then(setDetail)
      .catch((e: Error) => setError(e.message))
  }, [target.documentId])

  useEffect(load, [load])

  function changed() {
    setEditing(null)
    load()
    onChanged()
  }

  const deadline = target.kind === 'deadline' ? detail?.deadlines.find((d) => d.id === target.deadlineId) : undefined
  // 대기(송달일 입력·기일 확정)와 진행 중인 기한은 고칠 수 있다. 완료·취소는 보기만 한다.
  const editable = target.kind !== 'deadline' || deadline?.status === 'confirmed'
  const doc = detail?.extraction

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal popup" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="문서 상세">
        <header className="popup-header">
          <h2>{detail ? `${detail.document_type} · ${detail.case_number ?? '사건번호 없음'}` : '문서'}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </header>
        {error && <p className="issue error">{error}</p>}
        {!detail && !error && target.kind !== 'hearing' && <p className="muted">불러오는 중…</p>}

        {detail && doc && (
          <section className="popup-section">
            <div className="popup-section-head">
              <h3>문서 정보</h3>
              {editable && editing !== 'case' && (
                <button type="button" className="link-button" onClick={() => setEditing('case')}>
                  사건 정보 수정
                </button>
              )}
            </div>
            {editing === 'case' ? (
              <CaseForm detail={detail} onSaved={changed} onCancel={() => setEditing(null)} />
            ) : (
              <dl className="popup-dl">
                <dt>사건</dt>
                <dd>
                  {detail.case_number ?? '—'} {[detail.court, detail.case_name].filter(Boolean).join(' · ')}
                  {detail.corrected && <span className="status-tag">사람이 고침</span>}
                </dd>
                {detail.parties.length > 0 && (
                  <>
                    <dt>당사자</dt>
                    <dd>{detail.parties.map((p) => `${p.role} ${p.name}`).join(', ')}</dd>
                  </>
                )}
                {doc.issued_date && (
                  <>
                    <dt>발령일</dt>
                    <dd>{/^\d{4}-\d{2}-\d{2}$/.test(doc.issued_date.value) ? formatDate(doc.issued_date.value) : doc.issued_date.value}</dd>
                  </>
                )}
                {doc.order_summary && (
                  <>
                    <dt>명령 요지</dt>
                    <dd>{doc.order_summary.value}</dd>
                  </>
                )}
                {doc.designated_period && (
                  <>
                    <dt>정한 기간</dt>
                    <dd>
                      {doc.designated_period.amount}
                      {doc.designated_period.unit === '월' ? '개월' : doc.designated_period.unit}
                    </dd>
                  </>
                )}
                <dt>파일</dt>
                <dd>
                  <a href={fileUrl(detail.file_id)} target="_blank" rel="noreferrer">
                    {detail.filename}
                  </a>{' '}
                  <span className="muted">
                    · {formatDateTime(detail.created_at)} 받음 · {detail.model}
                  </span>
                </dd>
              </dl>
            )}
            {detail.issues
              .filter((i) => i.level === 'error')
              .map((i) => (
                <p key={i.field + i.message} className="issue error">
                  {i.message}
                </p>
              ))}
          </section>
        )}

        {target.kind === 'hearing' && target.hearing.status === 'tentative' && (
          <HearingSection hearing={target.hearing} onChanged={changed} />
        )}

        {target.kind === 'deadline' && deadline && (
          <section className="popup-section">
            <div className="popup-section-head">
              <h3>기한 · {deadline.label}</h3>
              {editable && editing !== 'terms' && (
                <button type="button" className="link-button" onClick={() => setEditing('terms')}>
                  기한 수정
                </button>
              )}
            </div>
            {editing === 'terms' ? (
              <TermsForm deadline={deadline} onSaved={changed} onCancel={() => setEditing(null)} />
            ) : (
              <DeadlineInfo d={deadline} />
            )}
          </section>
        )}

        {target.kind === 'document' && detail && (
          <DeadlinePanel
            key={detail.case_number ?? detail.document_id}
            suggestions={detail.suggestions}
            documentId={detail.document_id}
            fileId={detail.file_id}
            documentType={detail.document_type}
            caseNumber={detail.case_number}
            onConfirmed={changed}
          />
        )}
      </div>
    </div>
  )
}
