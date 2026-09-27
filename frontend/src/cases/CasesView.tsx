import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  addEvidence,
  deleteEvidence,
  downloadUrl,
  fileUrl,
  getCase,
  listCases,
  makeBrief,
  updateEvidence,
  type BriefResult,
  type CaseDetail,
  type CaseListItem,
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

// 준비서면 틀: 사건 정보·입증방법·첨부서류는 기록에서, 본문은 변호사가 쓴 글.
function BriefSection({ detail, onChanged }: { detail: CaseDetail; onChanged: () => void }) {
  const [side, setSide] = useState<'원고' | '피고'>(detail.facts.our_side === '피고' ? '피고' : '원고')
  const [title, setTitle] = useState('준비서면')
  const [agent, setAgent] = useState(detail.facts.brief_agent ?? '')
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState('')
  const ours: EvidenceSide = side === '원고' ? '갑' : '을'
  const candidates = detail.evidence_list.filter((e) => e.side === ours)
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(candidates.filter((e) => !e.submitted_on).map((e) => e.id)))
  const [result, setResult] = useState<BriefResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setResult(
        await makeBrief(detail.case_number, {
          side,
          title,
          agent,
          body,
          evidence_ids: candidates.filter((c) => chosen.has(c.id)).map((c) => c.id),
          attachments: attachments.split('\n').map((a) => a.trim()).filter(Boolean),
        }),
      )
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function markSubmitted() {
    const ids = candidates.filter((c) => chosen.has(c.id) && !c.submitted_on).map((c) => c.id)
    await Promise.all(ids.map((id) => updateEvidence(id, { submitted_on: todayIso() })))
    onChanged()
  }

  return (
    <section className="case-section">
      <h3>준비서면 틀 만들기</h3>
      <p className="muted">사건 정보, 입증방법, 첨부서류는 기록에서 채웁니다. 본문은 담당 변호사가 쓴 글을 붙여 넣거나 비워 두면 자리표시로 남깁니다.</p>
      <form className="brief-form" onSubmit={submit}>
        <div className="user-form-grid">
          <label>
            우리 측
            <select value={side} onChange={(e) => setSide(e.target.value as '원고' | '피고')}>
              <option value="원고">원고</option>
              <option value="피고">피고</option>
            </select>
          </label>
          <label>
            제목
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: 원고 제2준비서면" required />
          </label>
          <label>
            소송대리인(선택)
            <input value={agent} onChange={(e) => setAgent(e.target.value)} placeholder="예: 법무법인 ○○ 담당변호사 ○○○" />
          </label>
        </div>
        <label>
          본문(변호사 작성, 빈 줄로 문단 구분)
          <textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} placeholder="비워 두면 '[본문: 담당 변호사 작성]'으로 남깁니다." />
        </label>
        <fieldset className="brief-evidence-pick">
          <legend>입증방법에 넣을 {ours}호증</legend>
          {candidates.length === 0 && <p className="muted small-note">{ours}호증이 없습니다. 위 증거 목록에서 추가하세요.</p>}
          {candidates.map((c) => (
            <label key={c.id}>
              <input
                type="checkbox"
                checked={chosen.has(c.id)}
                onChange={(e) => {
                  const next = new Set(chosen)
                  if (e.target.checked) next.add(c.id)
                  else next.delete(c.id)
                  setChosen(next)
                }}
              />{' '}
              {c.label} {c.title} {c.submitted_on && <span className="muted">(이미 제출)</span>}
            </label>
          ))}
        </fieldset>
        <label>
          첨부서류 더하기(선택, 한 줄에 하나)
          <textarea rows={2} value={attachments} onChange={(e) => setAttachments(e.target.value)} placeholder="예: 소송위임장 1통" />
        </label>
        {error && <p className="issue error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? '만드는 중…' : '준비서면 틀 만들기'}
        </button>
      </form>
      {result && (
        <div className="brief-result">
          <a className="draft-file" href={downloadUrl(result.file_id)} download={result.filename}>
            <span className="file-icon docx" aria-hidden="true">
              DOCX
            </span>
            <span className="file-text">
              <span className="file-name">{result.filename}</span>
              <span className="file-detail">
                입증방법 {result.evidence.length ? result.evidence.join(', ') : '없음'}
                {result.body_empty && ' · 본문은 자리표시'}
              </span>
            </span>
          </a>
          {result.evidence.length > 0 && (
            <button type="button" className="link-button" onClick={markSubmitted}>
              넣은 증거를 오늘 제출로 표시
            </button>
          )}
        </div>
      )}
    </section>
  )
}

export default function CasesView({ initialCase, onOpenFile }: { initialCase?: string | null; onOpenFile: (fileId: string, name: string) => void }) {
  const [query, setQuery] = useState('')
  const [list, setList] = useState<CaseListItem[] | null>(null)
  const [selected, setSelected] = useState<string | null>(initialCase ?? null)
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [summaryOf, setSummaryOf] = useState<string | null>(null)

  const loadList = useCallback((q?: string) => {
    listCases(q)
      .then(setList)
      .catch((e: Error) => setError(e.message))
  }, [])

  const loadDetail = useCallback(() => {
    if (!selected) return
    getCase(selected)
      .then((d) => {
        setDetail(d)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [selected])

  useEffect(() => loadList(), [loadList])
  useEffect(loadDetail, [loadDetail])

  const summaryDoc = detail?.document_list.find((d) => d.document_id === summaryOf)

  return (
    <div className="cases-view">
      <aside className="case-list">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            loadList(query.trim() || undefined)
          }}
        >
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="사건번호·당사자·사건명" aria-label="사건 찾기" />
        </form>
        {list?.length === 0 && <p className="muted small-note">사건이 없습니다. 법원 문서를 채팅에 올리면 사건이 만들어집니다.</p>}
        <ul>
          {list?.map((c) => (
            <li key={c.case_number}>
              <button type="button" className={c.case_number === selected ? 'active' : undefined} onClick={() => setSelected(c.case_number)}>
                <strong>{c.case_number}</strong> {c.case_name}
                <span className="muted small-note">
                  {parties(c.parties)} · 진행 중 기한 {c.open_deadlines} · 증거 {c.evidence}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="case-detail">
        {error && <p className="issue error">{error}</p>}
        {!detail ? (
          <p className="muted">왼쪽에서 사건을 고르세요.</p>
        ) : (
          <>
            <header className="view-header">
              <div>
                <h1>
                  {detail.case_number} {detail.case_name}
                </h1>
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

            <EvidenceSection key={`e-${detail.case_number}`} detail={detail} onChanged={loadDetail} />
            <BriefSection key={`b-${detail.case_number}-${detail.evidence_list.length}`} detail={detail} onChanged={loadDetail} />
          </>
        )}
      </div>

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
              onEvidenceAdded={loadDetail}
            />
          </div>
        </div>
      )}
    </div>
  )
}
