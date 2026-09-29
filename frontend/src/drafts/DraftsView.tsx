import { useCallback, useEffect, useState } from 'react'
import {
  downloadUrl,
  getDraftDetail,
  listCases,
  listDrafts,
  reviewDraft,
  uploadFinal,
  type CaseListItem,
  type DraftDetail,
  type DraftListItem,
} from '../api'
import { useUser } from '../auth/UserContext'
import CaseMaterials from '../cases/CaseMaterials'
import { toIso } from '../calendar/dates'
import { formatDate } from '../format'
import BriefView from './BriefView'

const ALL = ''

function when(iso: string) {
  return formatDate(toIso(new Date(iso)))
}

function StatusTag({ label }: { label: DraftListItem['status_label'] }) {
  return <span className={`review-badge${label === '검토 전' ? '' : ' done'}`}>{label}</span>
}

// 초안 한 건의 내용과 할 일(내려받기·최종본 올리기·검토 완료·판례 넣기).
function DraftPanel({ id, onChanged, onOpenFile }: { id: string; onChanged: () => void; onOpenFile: (fileId: string, name: string) => void }) {
  const isLawyer = useUser().role === 'lawyer'
  const [detail, setDetail] = useState<DraftDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    getDraftDetail(id)
      .then((d) => {
        setDetail(d)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [id])

  useEffect(load, [load])

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      load()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!detail) return error ? <p className="issue error">{error}</p> : <p className="muted">불러오는 중…</p>

  return (
    <div className="draft-panel">
      <header className="view-header">
        <div>
          <h2>
            {detail.form_name} <StatusTag label={detail.status_label} />
          </h2>
          <p className="muted">
            {detail.case_number ?? '사건 없음'} {detail.case_name} · {detail.created_by} 작성 · {when(detail.created_at)}
            {detail.reviewed_by && ` · ${detail.reviewed_by} 검토`}
          </p>
        </div>
      </header>

      <a className="draft-file" href={downloadUrl(detail.file_id)} download={detail.filename}>
        <span className="file-icon docx" aria-hidden="true">
          DOCX
        </span>
        <span className="file-text">
          <span className="file-name">{detail.filename}</span>
          <span className="file-detail">Word 문서 · 눌러서 내려받기</span>
        </span>
      </a>
      {detail.blanks.length > 0 && <p className="issue warning">빈칸으로 둔 항목: {detail.blanks.join(', ')}. 제출 전에 채워야 합니다.</p>}
      {detail.final_file_id && (
        <p className="final-line">
          최종본:{' '}
          <a href={downloadUrl(detail.final_file_id)} download={detail.final_filename ?? undefined}>
            {detail.final_filename}
          </a>
          <span className="muted"> · 자료실에는 초안 대신 최종본이 들어갑니다</span>
        </p>
      )}

      <div className="review-actions">
        {isLawyer && !detail.reviewed_by && (
          <button type="button" className="secondary small" disabled={busy} onClick={() => run(() => reviewDraft(detail.id))}>
            검토 완료로 표시
          </button>
        )}
        <label className="secondary small file-button">
          {busy ? '올리는 중…' : detail.final_file_id ? '최종본 다시 올리기' : '최종본 올리기'}
          <input
            type="file"
            accept=".docx,.pdf"
            hidden
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) run(() => uploadFinal(detail.id, file))
            }}
          />
        </label>
      </div>
      {error && <p className="issue error">{error}</p>}

      {detail.fields.length > 0 && (
        <dl className="draft-fields">
          {detail.fields.map((f) => (
            <div key={f.label}>
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {detail.brief && (
        <BriefView
          key={`${detail.id}-${Object.keys(detail.brief.filled).length}`}
          draftId={detail.id}
          brief={detail.brief}
          caseNumber={detail.case_number}
          canInsertCitation={isLawyer}
          onChanged={onChanged}
        />
      )}

      {detail.case_number && (
        <details className="case-materials-toggle">
          <summary>이 사건의 자료 (받은 문서, 기한, 증거 목록)</summary>
          <CaseMaterials caseNumber={detail.case_number} onOpenFile={onOpenFile} />
        </details>
      )}
    </div>
  )
}

// 작성한 초안(서식·준비서면)을 사건별로 보는 화면. 초안은 채팅에서 만든다.
export default function DraftsView({
  initialCase,
  onOpenFile,
}: {
  initialCase?: string | null
  onOpenFile: (fileId: string, name: string) => void
}) {
  const [cases, setCases] = useState<CaseListItem[]>([])
  const [caseNumber, setCaseNumber] = useState(initialCase ?? ALL)
  const [formFilter, setFormFilter] = useState(ALL)
  const [drafts, setDrafts] = useState<DraftListItem[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listDrafts({ case_number: caseNumber || undefined, form_id: formFilter || undefined })
      .then((list) => {
        setDrafts(list)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [caseNumber, formFilter])

  useEffect(load, [load])
  useEffect(() => {
    listCases()
      .then(setCases)
      .catch(() => undefined)
  }, [])

  const forms = [...new Map((drafts ?? []).map((d) => [d.form_id, d.form_name])).entries()]
  const selectedDraft = drafts?.find((d) => d.id === selected) ?? null

  return (
    <div className="cases-view drafts-view">
      <aside className="case-list">
        <label className="filter-label">
          사건
          <select
            value={caseNumber}
            onChange={(e) => {
              setCaseNumber(e.target.value)
              setSelected(null)
            }}
          >
            <option value={ALL}>전체 사건</option>
            {cases.map((c) => (
              <option key={c.case_number} value={c.case_number}>
                {c.case_number} {c.case_name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-label">
          종류
          <select value={formFilter} onChange={(e) => setFormFilter(e.target.value)}>
            <option value={ALL}>전체</option>
            {forms.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {error && <p className="issue error">{error}</p>}
        {drafts?.length === 0 && (
          <p className="muted small-note">
            작성한 초안이 없습니다. 채팅에서 "준비서면 작성해줘"처럼 요청하면 여기에 쌓입니다.
          </p>
        )}
        <ul>
          {drafts?.map((d) => (
            <li key={d.id}>
              <button type="button" className={d.id === selected ? 'active' : undefined} onClick={() => setSelected(d.id)}>
                <strong>{d.form_name}</strong> <StatusTag label={d.status_label} />
                <span className="muted small-note">
                  {d.case_number ?? '사건 없음'} · {d.created_by} · {when(d.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="case-detail">
        {selectedDraft ? (
          <DraftPanel key={selectedDraft.id} id={selectedDraft.id} onChanged={load} onOpenFile={onOpenFile} />
        ) : caseNumber ? (
          <CaseMaterials key={caseNumber} caseNumber={caseNumber} onOpenFile={onOpenFile} />
        ) : (
          <p className="muted">왼쪽에서 초안을 고르거나, 사건을 골라 그 사건의 자료(받은 문서·기한·증거 목록)를 보세요.</p>
        )}
      </div>
    </div>
  )
}
