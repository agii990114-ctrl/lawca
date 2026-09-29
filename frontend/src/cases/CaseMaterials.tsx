import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  addEvidence,
  deleteEvidence,
  fileUrl,
  getCase,
  updateEvidence,
  type CaseDetail,
  type EvidenceItem,
  type EvidenceSide,
} from '../api'
import { dDay, daysUntil, formatDate, todayIso } from '../format'
import BriefSummaryView from './BriefSummaryView'

const SIDES: { side: EvidenceSide; label: string }[] = [
  { side: '갑', label: '갑호증 (원고)' },
  { side: '을', label: '을호증 (피고)' },
]

function parties(list: { role: string; name: string }[]) {
  return list.map((p) => `${p.role} ${p.name}`).join(' · ')
}

// 증거 한 줄. 제목·메모·번호를 고치고, 제출 여부를 표시한다.
function EvidenceRow({ item, onChanged }: { item: EvidenceItem; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ number: item.number, title: item.title, note: item.note })
  const [error, setError] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (editing) {
    return (
      <tr>
        <td>
          <input className="evidence-number" value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} />
        </td>
        <td colSpan={2}>
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="증거 이름" />
          <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="메모" />
          {error && <p className="issue error">{error}</p>}
        </td>
        <td className="actions">
          <button type="button" className="secondary small" onClick={() => run(() => updateEvidence(item.id, form))}>
            저장
          </button>
          <button type="button" className="link-button" onClick={() => setEditing(false)}>
            취소
          </button>
        </td>
      </tr>
    )
  }
  return (
    <tr>
      <td className="nowrap">
        <strong>{item.label}</strong>
      </td>
      <td>
        {item.title}
        {item.note && <div className="muted small-note">{item.note}</div>}
        {item.from_summary && <div className="muted small-note">상대방 서면에서 가져옴</div>}
        {error && <p className="issue error">{error}</p>}
      </td>
      <td className="nowrap">
        {item.submitted_on ? <span className="status-tag">{formatDate(item.submitted_on)} 제출</span> : <span className="muted">미제출</span>}
      </td>
      <td className="actions">
        {item.submitted_on ? (
          <button type="button" className="link-button" onClick={() => run(() => updateEvidence(item.id, { clear_submitted: true }))}>
            제출 취소
          </button>
        ) : (
          <button type="button" className="link-button" onClick={() => run(() => updateEvidence(item.id, { submitted_on: todayIso() }))}>
            제출 표시
          </button>
        )}
        <button type="button" className="link-button" onClick={() => setEditing(true)}>
          수정
        </button>
        <button
          type="button"
          className="link-button danger"
          onClick={() => window.confirm(`${item.label} ${item.title}을(를) 지울까요?`) && run(() => deleteEvidence(item.id))}
        >
          삭제
        </button>
      </td>
    </tr>
  )
}

function EvidenceSection({ detail, onChanged }: { detail: CaseDetail; onChanged: () => void }) {
  const [form, setForm] = useState<{ side: EvidenceSide; number: string; title: string; note: string }>({
    side: detail.facts.our_side === '피고' ? '을' : '갑',
    number: '',
    title: '',
    note: '',
  })
  const [error, setError] = useState<string | null>(null)

  async function add(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await addEvidence(detail.case_number, { ...form, number: form.number || undefined })
      setForm({ ...form, number: '', title: '', note: '' })
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="case-section">
      <h3>증거 목록</h3>
      <div className="evidence-columns">
        {SIDES.map(({ side, label }) => {
          const items = detail.evidence_list.filter((e) => e.side === side)
          return (
            <div key={side}>
              <h4>
                {label} <span className="muted">{items.length}건</span>
              </h4>
              {items.length === 0 ? (
                <p className="muted small-note">없음</p>
              ) : (
                <table className="users-table evidence-table">
                  <tbody>
                    {items.map((item) => (
                      <EvidenceRow key={item.id} item={item} onChanged={onChanged} />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )
        })}
      </div>
      <form className="evidence-add" onSubmit={add}>
        <select value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value as EvidenceSide })} aria-label="측">
          <option value="갑">갑</option>
          <option value="을">을</option>
          <option value="병">병</option>
        </select>
        <input className="evidence-number" value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} placeholder="번호(자동)" />
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="증거 이름 (예: 차용증 사본)" required />
        <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="메모(선택)" />
        <button type="submit" className="secondary small">
          추가
        </button>
      </form>
      {error && <p className="issue error">{error}</p>}
    </section>
  )
}

// 사건 자료: 받은 문서(상대방 서면 요약 보기), 기한, 증거 목록. 준비서면 초안은 본문에서 인용한 증거를 입증방법으로 넣는다.
export default function CaseMaterials({ caseNumber, onOpenFile }: { caseNumber: string; onOpenFile: (fileId: string, name: string) => void }) {
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [summaryOf, setSummaryOf] = useState<string | null>(null)

  const load = useCallback(() => {
    getCase(caseNumber)
      .then((d) => {
        setDetail(d)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [caseNumber])

  useEffect(load, [load])

  const summaryDoc = detail?.document_list.find((d) => d.document_id === summaryOf)

  return (
    <div className="case-materials">
      {error && <p className="issue error">{error}</p>}
      {detail && (
        <>
          <header className="view-header">
            <div>
              <h2>
                {detail.case_number} {detail.case_name}
              </h2>
              <p className="muted">
                {detail.court} · {parties(detail.parties)}
              </p>
            </div>
          </header>

          <section className="case-section">
            <h3>받은 문서</h3>
            <ul className="case-docs">
              {detail.document_list.map((d) => (
                <li key={d.document_id}>
                  <button type="button" className="link-button" onClick={() => onOpenFile(d.file_id, d.filename)}>
                    {d.document_type}
                  </button>{' '}
                  <span className="muted">
                    {d.issued_date ? formatDate(d.issued_date) : ''} · {d.filename}
                  </span>
                  {d.summary && (
                    <button type="button" className="secondary small" onClick={() => setSummaryOf(d.document_id)}>
                      요약 보기
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <section className="case-section">
            <h3>기한</h3>
            {detail.deadlines.length === 0 ? (
              <p className="muted small-note">확정한 기한이 없습니다.</p>
            ) : (
              <ul className="case-docs">
                {detail.deadlines.map((d) => (
                  <li key={d.id}>
                    <span className={`dday${d.status === 'confirmed' && daysUntil(d.deadline) <= 3 ? ' urgent' : ''}`}>
                      {d.status === 'confirmed' ? dDay(daysUntil(d.deadline)) : '완료'}
                    </span>{' '}
                    <strong>{formatDate(d.deadline)}</strong> {d.label}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <EvidenceSection key={`e-${detail.case_number}`} detail={detail} onChanged={load} />
        </>
      )}

      {summaryDoc?.summary && (
        <div className="modal-scrim" onClick={() => setSummaryOf(null)}>
          <div className="modal popup" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="서면 요약">
            <header className="popup-header">
              <h2>
                {summaryDoc.document_type} 요약 {summaryDoc.summary.submitter ? `· ${summaryDoc.summary.submitter}` : ''}
              </h2>
              <button type="button" className="icon-button" onClick={() => setSummaryOf(null)} aria-label="닫기">
                ✕
              </button>
            </header>
            <p className="muted small-note">
              <a href={fileUrl(summaryDoc.file_id)} target="_blank" rel="noreferrer">
                {summaryDoc.filename}
              </a>
            </p>
            <BriefSummaryView
              summary={summaryDoc.summary}
              documentId={summaryDoc.document_id}
              fileId={summaryDoc.file_id}
              caseNumber={detail?.case_number ?? null}
              onEvidenceAdded={load}
            />
          </div>
        </div>
      )}
    </div>
  )
}
