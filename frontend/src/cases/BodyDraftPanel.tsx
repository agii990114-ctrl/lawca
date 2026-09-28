import { useState } from 'react'
import { makeBriefBody, type BriefBodyResult, type CaseDetail } from '../api'

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
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      setResult(await makeBriefBody(detail.case_number, { side, notes, document_id: documentId || null }))
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
          <button type="button" className="primary small" onClick={() => onUse(result.body)}>
            아래 본문 칸에 넣기
          </button>
        </div>
      )}
    </div>
  )
}
