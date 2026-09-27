import { useState } from 'react'
import { dismissPending, type PendingDocument } from '../api'
import DeadlinePanel from '../DeadlinePanel'
import { formatDate } from '../format'

// 송달일을 넣어 기한을 확정해야 하는 문서. 여기서 바로 송달일을 넣고 확정할 수 있다.
export default function PendingList({
  items,
  onChanged,
  onOpenFile,
}: {
  items: PendingDocument[]
  onChanged: () => void
  onOpenFile: (fileId: string, name: string) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function dismiss(doc: PendingDocument) {
    if (!window.confirm(`${doc.document_type}(${doc.filename})의 기한을 잡지 않고 목록에서 뺄까요?`)) return
    try {
      await dismissPending(doc.document_id)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="pending">
      <h3>
        송달일 입력 대기 <span className="count">{items.length}</span>
      </h3>
      <p className="muted">기한을 아직 확정하지 않은 문서입니다. 송달일을 넣고 확정하면 캘린더에 올라갑니다.</p>
      {error && <p className="issue error">{error}</p>}
      <ul>
        {items.map((doc) => (
          <li key={doc.document_id} className={open === doc.document_id ? 'open' : undefined}>
            <button type="button" className="pending-head" onClick={() => setOpen(open === doc.document_id ? null : doc.document_id)}>
              <strong>{doc.document_type}</strong>
              <span>{doc.case_number ?? '사건번호 없음'}</span>
              <span className="muted">
                {doc.issued_date ? `${formatDate(doc.issued_date)} 발령` : doc.filename}
              </span>
            </button>
            {open === doc.document_id && (
              <div className="pending-body">
                <div className="pending-tools">
                  <button type="button" className="link-button" onClick={() => onOpenFile(doc.file_id, doc.filename)}>
                    문서 보기
                  </button>
                  <button type="button" className="link-button" onClick={() => dismiss(doc)}>
                    기한 없음(목록에서 빼기)
                  </button>
                </div>
                <DeadlinePanel
                  suggestions={doc.suggestions}
                  documentId={doc.document_id}
                  fileId={doc.file_id}
                  documentType={doc.document_type}
                  onConfirmed={() => {
                    setOpen(null)
                    onChanged()
                  }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
