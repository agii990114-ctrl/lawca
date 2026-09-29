import { useState } from 'react'
import { fileUrl, findCitations, insertCitation, type BriefPayload, type CitationCandidates } from '../api'

// 준비서면 초안의 내용: 점검 결과, 문단별 근거, 참고한 문서, 판례 후보.
// 점검은 먼저 볼 곳을 알려 줄 뿐이며 초안은 변호사가 검토한다.

function flagsOf(brief: BriefPayload): string[] {
  const c = brief.checks
  if (!c) return []
  return [
    ...(c.case_law.length ? [`판례 인용: ${c.case_law.join(', ')} (넣지 않도록 했는데 들어갔습니다. 지우거나 확인하세요)`] : []),
    ...(c.statutes_not_in_notes.length ? [`메모에 없는 법조문: ${c.statutes_not_in_notes.join(', ')}`] : []),
    ...(c.unknown_evidence.length ? [`증거 목록에 없는 증거: ${c.unknown_evidence.join(', ')}`] : []),
    ...(c.amounts_not_in_inputs.length ? [`자료에 없는 금액: ${c.amounts_not_in_inputs.join(', ')} (참고 문서에서 온 것일 수 있습니다)`] : []),
    ...(c.dates_not_in_inputs.length ? [`자료에 없는 날짜: ${c.dates_not_in_inputs.join(', ')} (참고 문서에서 온 것일 수 있습니다)`] : []),
    ...(c.foreign_names.length ? [`참고 문서에서 온 이름: ${c.foreign_names.join(', ')} (다른 사건의 당사자일 수 있습니다)`] : []),
    ...(c.paragraphs_without_source ? [`근거 표시가 없는 문단 ${c.paragraphs_without_source}개`] : []),
  ]
}

// [인용 확인 필요] 한 자리: 쟁점으로 대법원 판례 후보를 찾아 변호사가 골라 넣는다.
function CitationNeedRow({
  caseNumber,
  index,
  need,
  filled,
  editable,
  onPick,
}: {
  caseNumber: string
  index: number
  need: { issue: string; keywords: string[] }
  filled: string | null
  editable: boolean
  onPick: (citation: string) => Promise<void>
}) {
  const [found, setFound] = useState<CitationCandidates | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 본문에 [인용 확인 필요] 자리가 없어 넣지 못한 인용(워드에서 필요한 곳에 붙여 넣도록 보여 준다)
  const [manual, setManual] = useState<string | null>(null)

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

  async function pick(citation: string) {
    setError(null)
    setManual(null)
    try {
      await onPick(citation)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setManual(citation)
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
        ) : editable ? (
          <button type="button" className="secondary small" onClick={search} disabled={busy}>
            {busy ? '찾는 중…' : found ? '다시 찾기' : '판례 찾기'}
          </button>
        ) : null}
      </div>
      {error && <p className="issue error">{error}</p>}
      {manual && (
        <p className="issue warning">
          본문에 넣지 못했습니다. 워드에서 필요한 곳에 직접 붙여 넣으세요: <strong>{manual}</strong>{' '}
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
                <p className="small-note">판시사항: {item.holdings.length > 200 ? `${item.holdings.slice(0, 200)}…` : item.holdings}</p>
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
              <button type="button" className="link-button" onClick={() => pick(`(${item.citation} 참조)`)}>
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

export default function BriefView({
  draftId,
  brief: initial,
  caseNumber,
  canInsertCitation,
  showReferences = true,
  onChanged,
}: {
  draftId: string
  brief: BriefPayload
  caseNumber: string | null
  /** 판례 인용 넣기는 변호사만 */
  canInsertCitation: boolean
  showReferences?: boolean
  /** 인용을 넣어 DOCX가 바뀌었을 때 */
  onChanged?: () => void
}) {
  const [brief, setBrief] = useState(initial)
  const c = brief.checks
  const flags = flagsOf(brief)
  const filled = brief.filled

  return (
    <div className="brief-view">
      {brief.note && <p className="issue warning">{brief.note}</p>}
      {c && (
        <>
          <p className="muted small-note">
            {brief.model}
            {brief.opponent_document ? ` · 반박 대상: ${brief.opponent_document}` : ''} · [인용 확인 필요] {c.placeholders}곳
          </p>
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
        </>
      )}

      {showReferences && brief.references.length > 0 && (
        <section className="brief-refs">
          <h4>참고한 문서 (자료실)</h4>
          <ul>
            {brief.references.map((r) => (
              <li key={r.doc_id}>
                <span className="small-note">참고{r.number}</span>{' '}
                <a href={fileUrl(r.file_id, r.page ?? 1)} target="_blank" rel="noreferrer">
                  {r.title}
                </a>{' '}
                <span className="muted small-note">
                  {r.kind_label}
                  {r.status_label ? ` · ${r.status_label}` : ''} · {r.note_no ? `메모${r.note_no}에 대응` : '문체 참고'} ·{' '}
                  {r.used ? '본문에 반영' : '검색됨(본문에는 인용하지 않음)'}
                </span>
                <p className="small-note">{r.snippet}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {brief.sections.length > 0 && (
        <details className="references" open>
          <summary>본문과 문단별 근거</summary>
          {brief.sections.map((s) => (
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
      )}

      {brief.open_points.length > 0 && (
        <div className="muted small-note">
          확인할 점:
          <ul>
            {brief.open_points.map((o) => (
              <li key={o}>{o}</li>
            ))}
          </ul>
        </div>
      )}

      {brief.citation_needs.length > 0 && caseNumber && (
        <div className="citation-needs">
          <strong className="small-note">[인용 확인 필요] 자리의 판례 후보 (대법원, 국가법령정보센터)</strong>
          <ol>
            {brief.citation_needs.map((need, i) => (
              <CitationNeedRow
                key={i}
                caseNumber={caseNumber}
                index={i}
                need={need}
                filled={filled[String(i)] ?? null}
                editable={canInsertCitation}
                onPick={async (citation) => {
                  const updated = await insertCitation(draftId, i, citation)
                  if (updated.brief) setBrief(updated.brief)
                  onChanged?.()
                }}
              />
            ))}
          </ol>
          <p className="muted small-note">
            판례를 넣으면 초안 DOCX가 바로 바뀝니다. 넣기 전에 원문을 꼭 확인하세요. 넣지 않은 자리는 [인용 확인 필요]로 남습니다.
            {!canInsertCitation && ' (판례 넣기는 변호사만 할 수 있습니다)'}
          </p>
        </div>
      )}
    </div>
  )
}
