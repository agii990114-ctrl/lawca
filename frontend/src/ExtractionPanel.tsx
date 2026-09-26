import type { DocumentResult, Evidence, Issue } from './api'
import { formatDate } from './format'

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
  return rows
}

export default function ExtractionPanel({
  result,
  onShowPage,
}: {
  result: DocumentResult
  onShowPage: (page: number) => void
}) {
  const rows = rowsOf(result.extraction)
  const issuesByField = new Map<string, Issue[]>()
  for (const issue of result.issues) {
    issuesByField.set(issue.field, [...(issuesByField.get(issue.field) ?? []), issue])
  }
  const general = result.issues.filter((i) => !rows.some((r) => r.field === i.field))

  return (
    <section className="panel">
      <h2>문서에서 읽은 내용</h2>
      <p className="muted">
        값을 누르면 왼쪽 원문에서 해당 쪽을 엽니다. 모델: {result.model}
      </p>
      {general.length > 0 && (
        <ul className="issues">
          {general.map((i) => (
            <li key={i.field + i.message} className={`issue ${i.level}`}>
              {i.message}
            </li>
          ))}
        </ul>
      )}
      <table className="fields">
        <tbody>
          {rows.map((row) => {
            const issues = issuesByField.get(row.field) ?? []
            return (
              <tr key={row.field} className={issues.some((i) => i.level === 'error') ? 'has-error' : undefined}>
                <th scope="row">{row.label}</th>
                <td>
                  <button type="button" className="value" onClick={() => onShowPage(row.evidence.page)}>
                    {row.value}
                  </button>
                  <div className="evidence">
                    “{row.evidence.quote}” · {row.evidence.page}쪽
                  </div>
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
    </section>
  )
}
