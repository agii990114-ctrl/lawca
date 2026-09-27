import { fileUrl, type CaseSummary, type DeadlineRecord, type DeadlineResult, type SearchCardItem } from '../api'
import { dDay, daysUntil, formatDate } from '../format'

type OpenFile = (fileId: string, name: string) => void

export function DeadlineListCard({
  title,
  items,
  onOpenFile,
  onShowDeadlines,
}: {
  title: string
  items: DeadlineRecord[]
  onOpenFile: OpenFile
  onShowDeadlines: () => void
}) {
  return (
    <section className="card">
      <header className="card-header">
        <h3>{title}</h3>
        <button type="button" className="link-button" onClick={onShowDeadlines}>
          기한 화면에서 보기
        </button>
      </header>
      {items.length === 0 ? (
        <p className="muted">해당하는 기한이 없습니다. 기한은 문서 카드에서 확정해야 목록에 나옵니다.</p>
      ) : (
        <ul className="mini-list">
          {items.map((d) => {
            const days = daysUntil(d.deadline)
            const urgent = d.status === 'confirmed' && days <= 3
            return (
              <li key={d.id}>
                <span className={`dday${urgent ? ' urgent' : ''}`}>
                  {d.status === 'confirmed' ? dDay(days) : d.status === 'done' ? '완료' : '취소'}
                </span>
                <span className="mini-main">
                  <strong>{formatDate(d.deadline)}</strong> {d.label}
                  <span className="muted">
                    {' '}
                    · {d.case_number ?? '사건번호 없음'} {d.case_name ?? ''}
                  </span>
                </span>
                <button type="button" className="link-button" onClick={() => onOpenFile(d.file_id, d.filename)}>
                  원문
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

export function CaseListCard({ title, items, onOpenFile }: { title: string; items: CaseSummary[]; onOpenFile: OpenFile }) {
  return (
    <section className="card">
      <h3>{title}</h3>
      {items.length === 0 && <p className="muted">찾은 사건이 없습니다.</p>}
      {items.map((c) => (
        <div key={c.case_number} className="case-item">
          <div>
            <strong>{c.case_number}</strong> {c.case_name}
            <span className="muted"> · {c.court}</span>
            {c.open_deadlines > 0 && <span className="dday"> 진행 중 기한 {c.open_deadlines}</span>}
          </div>
          {c.parties.length > 0 && (
            <div className="muted">{c.parties.map((p) => `${p.role} ${p.name}`).join(' · ')}</div>
          )}
          {c.documents.length > 0 && (
            <div className="case-docs">
              {c.documents.map((d) => (
                <button key={d.file_id} type="button" className="chip" onClick={() => onOpenFile(d.file_id, d.filename)}>
                  {d.document_type}
                  {d.issued_date ? ` ${formatDate(d.issued_date)}` : ''}
                </button>
              ))}
            </div>
          )}
          {c.deadlines && c.deadlines.length > 0 && (
            <ul className="mini-list">
              {c.deadlines.map((d) => (
                <li key={`${d.deadline}-${d.label}`}>
                  <span className="dday">{d.status === '확정' ? dDay(d.days_left) : d.status}</span>
                  <span className="mini-main">
                    <strong>{formatDate(d.deadline)}</strong> {d.label}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  )
}

export function DeadlineCalcCard({ result }: { result: DeadlineResult & { label: string } }) {
  return (
    <section className="card">
      <h3>만료일 계산 · {result.label}</h3>
      <div className="deadline">
        <strong>{formatDate(result.deadline)}</strong>
        <span className="muted">
          {formatDate(result.event_date)} 기준 {result.period.label} · 저장하지 않은 계산값입니다
        </span>
      </div>
      <dl>
        <dt>기산일</dt>
        <dd>{formatDate(result.count_start)}</dd>
        <dt>연장 전 말일</dt>
        <dd>{formatDate(result.nominal_end)}</dd>
        {result.extended_over.length > 0 && (
          <>
            <dt>연장</dt>
            <dd>{result.extended_over.map((d) => `${formatDate(d.day)} ${d.reason}`).join(' → ')}</dd>
          </>
        )}
        <dt>근거</dt>
        <dd>{result.basis.join(', ')}</dd>
      </dl>
      {result.warnings.map((w) => (
        <p key={w} className="issue warning">
          {w}
        </p>
      ))}
    </section>
  )
}

// 자료실 검색 결과. 원문 조각을 그대로 보여 주고 원본 파일로 이어 준다.
export function SearchCard({ query, items, onShowLibrary }: { query: string; items: SearchCardItem[]; onShowLibrary: () => void }) {
  return (
    <section className="card">
      <header className="card-header">
        <h3>자료실 검색 · {query}</h3>
        <button type="button" className="link-button" onClick={onShowLibrary}>
          자료실에서 보기
        </button>
      </header>
      {items.length === 0 ? (
        <p className="muted">찾은 자료가 없습니다. 과거 서면·서식을 자료실에 올리면 검색할 수 있습니다.</p>
      ) : (
        <ul className="search-list">
          {items.map((item) => (
            <li key={item.doc_id}>
              <div className="search-title">
                <span className={`kind-tag ${item.kind}`}>{item.kind_label}</span>
                <a href={fileUrl(item.file_id, item.page ?? 1)} target="_blank" rel="noreferrer">
                  {item.title}
                </a>
                {item.status_label && <span className="status-tag">{item.status_label}</span>}
              </div>
              <p className="search-snippet">{item.snippet}</p>
              <p className="muted small-note">
                {[item.case_number, item.page ? `${item.page}쪽` : null, item.created_at].filter(Boolean).join(' · ')}
                {item.matched.includes('semantic') && !item.matched.includes('keyword') && ' · 뜻이 비슷한 자료'}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
