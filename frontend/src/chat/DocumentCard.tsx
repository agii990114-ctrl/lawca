import { useState } from 'react'
import type { DocumentResult, Evidence, Issue } from '../api'
import DeadlinePanel from '../DeadlinePanel'
import { formatDate } from '../format'

interface Row {
  field: string
  label: string
  value: string
  evidence: Evidence
}

type Item = { value: string; evidence: Evidence } | null

function rowsOf(doc: DocumentResult['extraction']): Row[] {
  const rows: Row[] = [
    { field: 'document_type', label: '문서 종류', value: doc.document_type, evidence: doc.document_type_evidence },
  ]
  const add = (field: string, label: string, item: Item, fmt = (v: string) => v) => {
    if (item) rows.push({ field, label, value: fmt(item.value), evidence: item.evidence })
  }
  add('court', '법원', doc.court)
  add('case_number', '사건번호', doc.case_number)
  add('case_name', '사건명', doc.case_name)
  doc.parties.forEach((p, i) =>
    rows.push({ field: `parties[${i}]`, label: p.role, value: p.name, evidence: p.evidence }),
  )
  add('issued_date', '발령일', doc.issued_date, (v) => (/^\d{4}-\d{2}-\d{2}$/.test(v) ? formatDate(v) : v))
  add('order_summary', '명령 요지', doc.order_summary)
  if (doc.designated_period) {
    const { amount, unit, evidence } = doc.designated_period
    const value = `${amount}${unit === '월' ? '개월' : unit}`
    rows.push({ field: 'designated_period', label: '문서가 정한 기간', value, evidence })
  }
  if (doc.hearing) {
    const { date, time, kind, place, evidence } = doc.hearing
    const when = /^\d{4}-\d{2}-\d{2}$/.test(date) ? formatDate(date) : date
    const value = [when, time, place].filter(Boolean).join(' · ') + ' (기한 목록 대기 탭에서 확정)'
    rows.push({ field: 'hearing', label: kind || '기일', value, evidence })
  }
  return rows
}

export default function DocumentCard({
  result,
  onShowPage,
}: {
  result: DocumentResult
  onShowPage: (page: number) => void
}) {
  const [done, setDone] = useState<Set<number>>(new Set())
  const rows = rowsOf(result.extraction)
  const issuesByField = new Map<string, Issue[]>()
  for (const issue of result.issues) {
    issuesByField.set(issue.field, [...(issuesByField.get(issue.field) ?? []), issue])
  }
  const general = result.issues.filter((i) => !rows.some((r) => r.field === i.field))

  function toggle(i: number) {
    const next = new Set(done)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    setDone(next)
  }

  return (
    <div className="result-cards">
      <section className="card">
        <header className="card-header">
          <h3>문서에서 읽은 내용</h3>
          <button type="button" className="link-button" onClick={() => onShowPage(1)}>
            원문 보기
          </button>
        </header>
        {general.map((i) => (
          <p key={i.field + i.message} className={`issue ${i.level}`}>
            {i.message}
          </p>
        ))}
        <table className="fields">
          <tbody>
            {rows.map((row) => {
              const issues = issuesByField.get(row.field) ?? []
              return (
                <tr key={row.field} className={issues.some((i) => i.level === 'error') ? 'has-error' : undefined}>
                  <th scope="row">{row.label}</th>
                  <td>
                    <div>{row.value}</div>
                    <button type="button" className="evidence" onClick={() => onShowPage(row.evidence.page)}>
                      “{row.evidence.quote}” · {row.evidence.page}쪽
                    </button>
                    {issues.map((i) => (
                      <div key={i.message} className={`issue ${i.level}`}>
                        {i.message}
                      </div>
                    ))}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="card-foot">근거를 누르면 원문의 해당 쪽이 열립니다 · 모델 {result.model}</p>
      </section>

      <DeadlinePanel
        suggestions={result.suggestions}
        documentId={result.document_id}
        fileId={result.file_id}
        documentType={result.extraction.document_type}
        caseNumber={result.extraction.case_number?.value}
      />

      <section className="card">
        <h3>할 일</h3>
        <ul className="checklist">
          {result.checklist.map((item, i) => (
            <li key={item}>
              <label>
                <input type="checkbox" checked={done.has(i)} onChange={() => toggle(i)} />
                <span className={done.has(i) ? 'done' : undefined}>{item}</span>
              </label>
            </li>
          ))}
        </ul>
        <p className="card-foot">체크 상태는 아직 저장되지 않습니다.</p>
      </section>
    </div>
  )
}
