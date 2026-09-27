import { useState } from 'react'
import { addEvidenceBulk, fileUrl, type BriefSummary } from '../api'

// 상대방 서면 요약. 주장마다 원문 문구와 쪽을 보여 주고, 원문에서 찾지 못한 문구는 확인 필요로 표시한다.
export default function BriefSummaryView({
  summary,
  documentId,
  fileId,
  caseNumber,
  onEvidenceAdded,
}: {
  summary: BriefSummary
  documentId: string
  fileId: string
  caseNumber: string | null
  onEvidenceAdded?: () => void
}) {
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function addAll() {
    if (!caseNumber) return
    setError(null)
    try {
      const { added, skipped } = await addEvidenceBulk(caseNumber, documentId, summary.evidence)
      setResult(
        [added.length ? `${added.join(', ')} 추가` : null, skipped.length ? `${skipped.join(', ')}는 이미 있음` : null]
          .filter(Boolean)
          .join(' · '),
      )
      onEvidenceAdded?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="brief-summary">
      {summary.request_summary && (
        <p className="brief-request">
          <span className="muted">구하는 결론</span> {summary.request_summary}
        </p>
      )}
      <ol className="brief-claims">
        {summary.claims.map((c, i) => (
          <li key={i}>
            <strong>{c.point}</strong>
            <p>{c.detail}</p>
            <p className="brief-quote">
              <a href={fileUrl(fileId, c.page)} target="_blank" rel="noreferrer">
                {c.page}쪽
              </a>{' '}
              “{c.quote}”
              {!c.verified && <span className="issue warning inline"> 원문에서 문구를 찾지 못했습니다. 원문과 대조하세요</span>}
            </p>
          </li>
        ))}
      </ol>
      {summary.evidence.length > 0 && (
        <div className="brief-evidence">
          <h4>상대방이 낸 증거</h4>
          <ul>
            {summary.evidence.map((e) => (
              <li key={e.label}>
                <strong>{e.label}</strong> {e.title}
              </li>
            ))}
          </ul>
          {caseNumber ? (
            <button type="button" className="secondary small" onClick={addAll} disabled={result !== null}>
              사건 증거 목록에 추가 ({summary.evidence.length}건)
            </button>
          ) : (
            <p className="muted small-note">사건번호를 읽지 못해 증거 목록에 넣을 수 없습니다.</p>
          )}
          {result && <p className="issue ok">{result}</p>}
          {error && <p className="issue error">{error}</p>}
        </div>
      )}
      <p className="card-foot">서면에 적힌 내용을 옮긴 요약입니다. 주장의 옳고 그름은 담당 변호사가 판단합니다.</p>
    </div>
  )
}
