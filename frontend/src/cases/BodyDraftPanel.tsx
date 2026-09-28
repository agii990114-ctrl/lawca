import { useState } from 'react'
import { findCitations, makeBriefBody, type BriefBodyResult, type CaseDetail, type CitationCandidates } from '../api'

const PLACEHOLDER = '[인용 확인 필요]'

// n번째 "[인용 확인 필요]"를 바꾼다(없으면 그대로).
function replaceNth(text: string, n: number, value: string) {
  let index = -1
  for (let i = 0; i <= n; i++) {
    index = text.indexOf(PLACEHOLDER, index + 1)
    if (index < 0) return text
  }
  return text.slice(0, index) + value + text.slice(index + PLACEHOLDER.length)
}

// [인용 확인 필요] 한 자리: 쟁점으로 대법원 판례를 찾아 골라 넣는다.
function CitationNeedRow({
  caseNumber,
  index,
  need,
  filled,
  onPick,
}: {
  caseNumber: string
  index: number
  need: { issue: string; keywords: string[] }
  filled: string | null
  onPick: (citation: string) => boolean
}) {
  // 본문에 [인용 확인 필요] 자리가 없어 넣지 못한 판례(직접 붙여 넣도록 보여 준다)
  const [manual, setManual] = useState<string | null>(null)
  const [found, setFound] = useState<CitationCandidates | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function search() {
    setBusy(true)
    setError(null)
    try {
      setFound(await findCitations(caseNumber, need))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="citation-need">
      <div className="citation-head">
        <span>
          <strong>{index + 1}.</strong> {need.issue}
        </span>
        {filled ? (
          <span className="status-tag">넣음: {filled}</span>
        ) : (
          <button type="button" className="secondary small" onClick={search} disabled={busy}>
            {busy ? '찾는 중…' : found ? '다시 찾기' : '판례 찾기'}
          </button>
        )}
      </div>
      {error && <p className="issue error">{error}</p>}
      {manual && (
        <p className="issue warning">
          본문에 [인용 확인 필요] 자리가 없어 넣지 못했습니다. 필요한 곳에 직접 붙여 넣으세요: <strong>{manual}</strong>{' '}
          <button type="button" className="link-button" onClick={() => navigator.clipboard.writeText(manual).catch(() => undefined)}>
            복사
          </button>
        </p>
      )}
      {found && !filled && (
        <div className="citation-results">
          <p className="muted small-note">국가법령정보센터에 보낸 검색어: {found.query} (이름·숫자는 뺐습니다)</p>
          {found.items.length === 0 && <p className="muted small-note">관련 대법원 판례를 찾지 못했습니다. 쟁점을 바꿔 직접 찾아 보세요.</p>}
          {found.items.map((item) => (
            <div key={item.id} className="citation-card">
              <div className="citation-title">
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.citation}
                </a>
                <span className="muted small-note">{item.case_name}</span>
              </div>
              {item.holdings && (
                <p className="small-note">
                  판시사항: {item.holdings.length > 200 ? `${item.holdings.slice(0, 200)}…` : item.holdings}
                </p>
              )}
              {item.holdings.length > 200 && (
                <details>
                  <summary className="small-note">판시사항 더 보기</summary>
                  <p className="small-note">{item.holdings}</p>
                </details>
              )}
              {item.summary && (
                <details>
                  <summary className="small-note">판결요지</summary>
                  <p className="small-note">{item.summary}</p>
                </details>
              )}
              {item.references.length > 0 && <p className="muted small-note">참조조문: {item.references.join(', ')}</p>}
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  const text = `(${item.citation} 참조)`
                  if (!onPick(text)) setManual(text)
                }}
              >
                원문을 확인했음 · 이 판례 넣기
              </button>
            </div>
          ))}
          {found.statutes.length > 0 && (
            <p className="muted small-note">
              관련 조문:{' '}
              {found.statutes.map((st, i) => (
                <span key={st.reference}>
                  {i > 0 && ', '}
                  {st.url ? (
                    <a href={st.url} target="_blank" rel="noreferrer">
                      {st.reference}
                    </a>
                  ) : (
                    st.reference
                  )}
                </span>
              ))}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

// 준비서면 본문 초안(변호사). 메모를 상대방 주장과 대응시켜 풀어 쓰고, 코드가 먼저 볼 곳을 알려 준다.
export default function BodyDraftPanel({
  detail,
  side,
  onUse,
}: {
  detail: CaseDetail
  side: '원고' | '피고'
  onUse: (body: string) => void
}) {
  const filings = detail.document_list.filter((d) => d.summary)
  const [documentId, setDocumentId] = useState(filings[0]?.document_id ?? '')
  const [notes, setNotes] = useState('')
  const [result, setResult] = useState<BriefBodyResult | null>(null)
  // 판례를 넣으며 고쳐 가는 본문과 자리별로 넣은 판례
  const [draftBody, setDraftBody] = useState('')
  const [filled, setFilled] = useState<Record<number, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const out = await makeBriefBody(detail.case_number, { side, notes, document_id: documentId || null })
      setResult(out)
      setDraftBody(out.body)
      setFilled({})
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const c = result?.checks
  const flags = c
    ? [
        ...(c.case_law.length ? [`판례 인용: ${c.case_law.join(', ')} (넣지 않도록 했는데 들어갔습니다. 지우거나 확인하세요)`] : []),
        ...(c.statutes_not_in_notes.length ? [`메모에 없는 법조문: ${c.statutes_not_in_notes.join(', ')}`] : []),
        ...(c.unknown_evidence.length ? [`증거 목록에 없는 증거: ${c.unknown_evidence.join(', ')}`] : []),
        ...(c.amounts_not_in_inputs.length ? [`자료에 없는 금액: ${c.amounts_not_in_inputs.join(', ')}`] : []),
        ...(c.dates_not_in_inputs.length ? [`자료에 없는 날짜: ${c.dates_not_in_inputs.join(', ')}`] : []),
        ...(c.paragraphs_without_source ? [`근거 표시가 없는 문단 ${c.paragraphs_without_source}개`] : []),
      ]
    : []

  return (
    <div className="body-draft">
      <h4>본문 초안 받기 (변호사)</h4>
      <p className="muted small-note">
        메모를 한 줄에 한 가지씩 적으면, 상대방 주장과 대응시켜 준비서면 문장으로 풀어 씁니다. 근거는 메모·상대방 서면 요약·증거 목록뿐이고,
        판례·법조문은 만들지 않고 [인용 확인 필요]로 남깁니다.
      </p>
      <label>
        반박할 상대방 서면
        <select value={documentId} onChange={(e) => setDocumentId(e.target.value)}>
          {filings.length === 0 && <option value="">요약한 상대방 서면 없음</option>}
          {filings.map((d) => (
            <option key={d.document_id} value={d.document_id}>
              {d.document_type} · {d.filename}
            </option>
          ))}
        </select>
      </label>
      <label>
        메모(한 줄에 한 가지)
        <textarea
          rows={5}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={"- 증여 아님: 이체 때 통장 표시 '대여금'(갑1)\n- 문자는 변제 약속 → 채무 승인\n- 소멸시효 중단(법리는 내가 넣음)"}
        />
      </label>
      <button type="button" className="secondary" onClick={run} disabled={busy || !notes.trim()}>
        {busy ? '초안 쓰는 중…' : '본문 초안 받기'}
      </button>
      {error && <p className="issue error">{error}</p>}

      {result && c && (
        <div className="body-draft-result">
          <p className="muted small-note">
            {result.model}
            {result.opponent_document ? ` · 반박 대상: ${result.opponent_document}` : ''} · [인용 확인 필요] {c.placeholders}곳
          </p>
          {result.examples_used.length > 0 && (
            <p className="muted small-note">
              문체 참고(자료실): {result.examples_used.map((e) => e.title).join(', ')} — 사실·금액·증거는 가져오지 않도록 했고, 베껴 온 금액·날짜는 아래 점검에 걸립니다.
            </p>
          )}
          {!c.notes_tracked && <p className="issue warning">메모 반영 여부를 확인하지 못했습니다(초안이 메모 번호를 달지 않음). 직접 대조하세요.</p>}
          {c.unused_notes.length > 0 && (
            <div className="issue warning">
              반영되지 않은 메모:
              <ul>
                {c.unused_notes.map((n) => (
                  <li key={n.number}>
                    메모{n.number}: {n.text}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {flags.map((f) => (
            <p key={f} className="issue warning">
              {f}
            </p>
          ))}
          {c.notes_tracked && c.unused_notes.length === 0 && flags.length === 0 && (
            <p className="issue ok">자동 점검에서 걸린 것이 없습니다. 표현과 법리는 직접 검토하세요.</p>
          )}
          <details className="references" open>
            <summary>초안과 문단별 근거</summary>
            {result.sections.map((s) => (
              <div key={s.heading} className="draft-section">
                <strong>{s.heading}</strong>
                {s.paragraphs.map((p, i) => (
                  <p key={i}>
                    {p.text} <span className="source-tags">{p.sources.join(' · ')}</span>
                  </p>
                ))}
              </div>
            ))}
          </details>
          {result.open_points.length > 0 && (
            <div className="muted small-note">
              확인할 점:
              <ul>
                {result.open_points.map((o) => (
                  <li key={o}>{o}</li>
                ))}
              </ul>
            </div>
          )}
          {result.citation_needs.length > 0 && (
            <div className="citation-needs">
              <strong className="small-note">[인용 확인 필요] 자리의 판례 후보 (대법원, 국가법령정보센터)</strong>
              <ol>
                {result.citation_needs.map((need, i) => (
                  <CitationNeedRow
                    key={i}
                    caseNumber={detail.case_number}
                    index={i}
                    need={need}
                    filled={filled[i] ?? null}
                    onPick={(citation) => {
                      // 앞에서 이미 바꾼 자리를 빼고 센 순서로 바꾼다. 바꿀 자리가 없으면 false(직접 붙여 넣기 안내)
                      const before = Object.keys(filled).filter((k) => Number(k) < i).length
                      const next = replaceNth(draftBody, i - before, citation)
                      if (next === draftBody) return false
                      setDraftBody(next)
                      setFilled((all) => ({ ...all, [i]: citation }))
                      return true
                    }}
                  />
                ))}
              </ol>
              <p className="muted small-note">판례는 넣기 전에 원문을 꼭 확인하세요. 넣지 않은 자리는 [인용 확인 필요]로 남습니다.</p>
            </div>
          )}
          <button type="button" className="primary small" onClick={() => onUse(draftBody)}>
            아래 본문 칸에 넣기
          </button>
        </div>
      )}
    </div>
  )
}
