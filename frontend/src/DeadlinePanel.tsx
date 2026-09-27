import { useEffect, useId, useState } from 'react'
import {
  computeDeadline,
  confirmDeadline,
  listDeadlines,
  SERVICE_LABELS,
  type DeadlineRecord,
  type DeadlineResult,
  type Period,
  type ServiceKind,
  type Suggestion,
  type Unit,
} from './api'
import { dDay, daysUntil, formatDate, todayIso } from './format'

const MANUAL = -1

interface Request {
  event_date: string
  service_kind: ServiceKind
  rule_id?: string
  period?: Period
  label: string
}

// 송달일을 받아 기한을 계산하고 확정한다.
// 같은 사건(사건번호가 없으면 같은 문서)에 같은 종류로 진행 중인 기한이 있으면 그 종류는 확정할 수 없다.
// 확정한 기한은 여기서 고치지 않고 기한 목록에서 고친다.
export default function DeadlinePanel({
  suggestions,
  documentId,
  fileId,
  documentType,
  caseNumber,
  onConfirmed,
}: {
  suggestions: Suggestion[]
  documentId?: string | null
  fileId: string
  documentType: string
  caseNumber?: string | null
  onConfirmed?: (record: DeadlineRecord) => void
}) {
  const [open, setOpen] = useState<DeadlineRecord[] | null>(null)
  const [choice, setChoice] = useState(0)
  const [manual, setManual] = useState<{ amount: number; unit: Unit }>({ amount: 7, unit: '일' })
  const [eventDate, setEventDate] = useState(todayIso())
  const [service, setService] = useState<ServiceKind>('electronic_confirmed')
  const [result, setResult] = useState<{ request: Request; value: DeadlineResult } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const radioName = useId()

  // 이 문서와 같은 사건에서 진행 중인 기한(문서를 읽은 뒤 DB에서 확인한다).
  useEffect(() => {
    const byDocument = documentId ? listDeadlines({ document_id: documentId, status: ['confirmed', 'done'] }) : Promise.resolve([])
    const byCase = caseNumber ? listDeadlines({ case_number: caseNumber, status: ['confirmed'] }) : Promise.resolve([])
    Promise.all([byDocument, byCase])
      .then(([a, b]) => {
        const all = new Map([...a, ...b].map((d) => [d.id, d]))
        setOpen([...all.values()].sort((x, y) => x.deadline.localeCompare(y.deadline)))
      })
      .catch(() => setOpen([]))
  }, [documentId, caseNumber])

  const manualKey = `designated:${documentType}`
  const taken = new Set((open ?? []).filter((d) => d.status === 'confirmed').map((d) => d.key))
  const available = suggestions.filter((s) => !taken.has(s.key))
  const manualAllowed = !taken.has(manualKey)
  const choices = available.length + (manualAllowed ? 1 : 0)
  const current = choice >= 0 && choice < available.length ? choice : available.length > 0 ? 0 : MANUAL
  const selected = current === MANUAL ? null : available[current]

  function currentRequest(): Request {
    const base = { event_date: eventDate, service_kind: service }
    if (selected?.kind === 'statutory' && selected.rule_id) {
      return { ...base, rule_id: selected.rule_id, label: selected.label }
    }
    if (selected?.period) {
      return { ...base, period: selected.period, label: `${documentType} 기한` }
    }
    return { ...base, period: { ...manual, label: '' }, label: '직접 입력한 기한' }
  }

  // 계산한 뒤 입력을 바꾸면 확정하기 전에 다시 계산해야 한다.
  const stale = result !== null && JSON.stringify(result.request) !== JSON.stringify(currentRequest())

  async function calculate() {
    setBusy(true)
    setError(null)
    setResult(null)
    const request = currentRequest()
    try {
      const value = await computeDeadline({
        event_date: request.event_date,
        deemed_electronic_service: request.service_kind === 'electronic_deemed',
        ...(request.rule_id ? { rule_id: request.rule_id } : { period: request.period }),
      })
      setResult({ request, value })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!result) return
    setBusy(true)
    setError(null)
    try {
      const record = await confirmDeadline({ ...result.request, document_id: documentId, file_id: fileId })
      setOpen((all) => [...(all ?? []), record].sort((a, b) => a.deadline.localeCompare(b.deadline)))
      setResult(null)
      setChoice(0)
      onConfirmed?.(record)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (open === null) return null

  return (
    <section className="card">
      <h3>기한 계산</h3>

      {open.length > 0 && (
        <>
          <ul className="confirmed-list">
            {open.map((d) => {
              const days = daysUntil(d.deadline)
              return (
                <li key={d.id} className={d.status}>
                  <span className={`dday${days <= 3 && d.status === 'confirmed' ? ' urgent' : ''}`}>
                    {d.status === 'done' ? '완료' : dDay(days)}
                  </span>
                  <span>
                    <strong>{formatDate(d.deadline)}</strong> {d.label}
                    <span className="muted">
                      {' '}
                      · 송달 {formatDate(d.event_date)}
                      {d.document_id !== documentId ? ' · 같은 사건의 다른 문서' : ''}
                    </span>
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="muted">확정한 기한은 여기서 고칠 수 없습니다. 고치려면 기한 목록에서 수정하세요.</p>
        </>
      )}

      {choices === 0 ? null : (
        <>
          <fieldset>
            <legend>기간</legend>
            {available.map((s, i) => (
              <label key={s.key} className="choice">
                <input type="radio" name={radioName} checked={current === i} onChange={() => setChoice(i)} />
                <span>
                  {s.label}
                  {s.period && s.kind === 'statutory' ? ` · ${s.period.label}` : ''}
                  {s.note && <span className="muted"> · {s.note}</span>}
                </span>
              </label>
            ))}
            {manualAllowed && (
              <label className="choice">
                <input type="radio" name={radioName} checked={current === MANUAL} onChange={() => setChoice(MANUAL)} />
                <span>직접 입력</span>
                {current === MANUAL && (
                  <span className="inline-inputs">
                    <input
                      type="number"
                      min={1}
                      value={manual.amount}
                      onChange={(e) => setManual({ ...manual, amount: Number(e.target.value) })}
                      aria-label="기간 숫자"
                    />
                    <select
                      value={manual.unit}
                      onChange={(e) => setManual({ ...manual, unit: e.target.value as Unit })}
                      aria-label="기간 단위"
                    >
                      <option value="일">일</option>
                      <option value="주">주</option>
                      <option value="월">개월</option>
                      <option value="년">년</option>
                    </select>
                  </span>
                )}
              </label>
            )}
          </fieldset>

          <fieldset>
            <legend>송달일</legend>
            <p className="muted">송달일은 문서에 적혀 있지 않습니다. 직접 입력하세요.</p>
            <div className="row">
              <input type="date" value={eventDate} onChange={(e) => setEventDate(e.target.value)} aria-label="송달일" />
              <select value={service} onChange={(e) => setService(e.target.value as ServiceKind)} aria-label="송달 유형">
                {Object.entries(SERVICE_LABELS).map(([kind, label]) => (
                  <option key={kind} value={kind}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </fieldset>

          <button type="button" className="secondary" onClick={calculate} disabled={busy || !eventDate}>
            {busy && !result ? '계산 중…' : '기한 계산'}
          </button>
        </>
      )}

      {error && <p className="issue error">{error}</p>}

      {result && (
        <div className="result">
          <div className="deadline">
            <span className="muted">만료일</span>
            <strong>{formatDate(result.value.deadline)}</strong>
            <span className="muted">이날이 끝날 때 기간이 만료됩니다.</span>
          </div>
          <dl>
            <dt>기간</dt>
            <dd>{result.value.period.label}</dd>
            <dt>기산일</dt>
            <dd>{formatDate(result.value.count_start)}</dd>
            <dt>연장 전 말일</dt>
            <dd>{formatDate(result.value.nominal_end)}</dd>
            {result.value.extended_over.length > 0 && (
              <>
                <dt>연장</dt>
                <dd>{result.value.extended_over.map((d) => `${formatDate(d.day)} ${d.reason}`).join(' → ')}</dd>
              </>
            )}
            <dt>근거</dt>
            <dd>{result.value.basis.join(', ')}</dd>
          </dl>
          {result.value.warnings.map((w) => (
            <p key={w} className="issue warning">
              {w}
            </p>
          ))}
          <div className="confirm-row">
            <button type="button" className="primary" onClick={confirm} disabled={busy || stale}>
              {busy ? '저장 중…' : '기한 확정'}
            </button>
            <span className="muted">
              {stale
                ? '입력이 바뀌었습니다. 다시 계산한 뒤 확정하세요.'
                : '송달일과 기간을 원문과 대조한 뒤 확정하세요. 확정하면 기한 목록과 캘린더에 올라갑니다.'}
            </span>
          </div>
        </div>
      )}
    </section>
  )
}
