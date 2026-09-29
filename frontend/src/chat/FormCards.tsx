import { useEffect, useId, useState } from 'react'
import {
  downloadUrl,
  getDraft,
  getJob,
  LATER,
  reviewDraft,
  uploadFinal,
  type DraftCardData,
  type DraftStatus,
  type QuestionCardData,
  type QuestionField,
} from '../api'
import { useUser } from '../auth/UserContext'
import BriefView from '../drafts/BriefView'
import { ReferenceList } from './QueryCards'

export type Answer = (jobId: string, answers: Record<string, string>, summary: string) => Promise<void>

function summarize(fields: QuestionField[], answers: Record<string, string>): string {
  return fields
    .map((f) => [f.label, answers[f.key] === LATER ? '나중에 입력' : answers[f.key]] as const)
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}: ${value}`)
    .join('\n')
}

function Input({
  field,
  value,
  disabled,
  onChange,
}: {
  field: QuestionField
  value: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  const id = useId()
  const common = { id, disabled, value, 'aria-label': field.label }
  let control
  if (field.type === 'select') {
    control = (
      <select {...common} onChange={(e) => onChange(e.target.value)}>
        <option value="">선택하세요</option>
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  } else if (field.type === 'textarea') {
    control = <textarea {...common} rows={2} onChange={(e) => onChange(e.target.value)} />
  } else {
    const type = field.type === 'date' ? 'date' : field.type === 'number' ? 'number' : 'text'
    control = <input {...common} type={type} min={type === 'number' ? 1 : undefined} onChange={(e) => onChange(e.target.value)} />
  }
  return (
    <label className="question-field" htmlFor={id}>
      <span className="question-label">{field.label}</span>
      {control}
      {field.help && <span className="muted">{field.help}</span>}
      {!disabled && field.suggestions && (
        <span className="suggestions">
          <span className="muted small-note">예전에 검토를 마친 초안의 문구 (누르면 채워집니다)</span>
          {field.suggestions.map((s) => (
            <button key={s.text} type="button" className="secondary small suggestion" title={s.text} onClick={() => onChange(s.text)}>
              {s.text.length > 60 ? `${s.text.slice(0, 60)}…` : s.text}
              <span className="muted small-note"> · {s.case_number || '사건 없음'} · {s.when}</span>
            </button>
          ))}
        </span>
      )}
    </label>
  )
}

export function QuestionCard({ data, onAnswer }: { data: QuestionCardData; onAnswer: Answer }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(data.fields.map((f) => [f.key, f.default ?? ''])),
  )
  const [later, setLater] = useState<Record<string, boolean>>({})
  const [state, setState] = useState<'open' | 'sending' | 'answered'>('open')
  const [error, setError] = useState<string | null>(null)

  // 다시 열었을 때 이미 답한 질문인지 확인한다. 작업이 끝났거나 같은 작업이 다른 질문을 기다리면 답한 것이다.
  // 진행 중(running)이면 막 나온 질문일 수 있으므로 열어 둔다.
  useEffect(() => {
    getJob(data.job_id)
      .then((job) => {
        const otherQuestion = job.status === 'waiting' && job.question?.question_id !== data.question_id
        if (otherQuestion || ['done', 'error', 'stopped'].includes(job.status)) setState('answered')
      })
      .catch(() => undefined)
  }, [data.job_id, data.question_id])

  const answers = Object.fromEntries(data.fields.map((f) => [f.key, later[f.key] ? LATER : values[f.key].trim()]))
  const complete = data.fields.every((f) => answers[f.key])
  const disabled = state !== 'open'

  async function submit() {
    setState('sending')
    setError(null)
    try {
      await onAnswer(data.job_id, answers, summarize(data.fields, answers))
      setState('answered')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setState('open')
    }
  }

  return (
    <section className={`card question-card${disabled ? ' answered' : ''}`}>
      <h3>{data.title}</h3>
      {data.message && <p className="muted">{data.message}</p>}
      {data.references && <ReferenceList items={data.references} open={state === 'open'} />}
      {data.errors.map((e) => (
        <p key={e} className="issue error">
          {e}
        </p>
      ))}
      <div className="question-fields">
        {data.fields.map((f) => (
          <div key={f.key}>
            <Input
              field={f}
              value={values[f.key]}
              disabled={disabled || !!later[f.key]}
              onChange={(v) => setValues((all) => ({ ...all, [f.key]: v }))}
            />
            {f.allow_later && (
              <label className="later-toggle">
                <input
                  type="checkbox"
                  checked={!!later[f.key]}
                  disabled={disabled}
                  onChange={(e) => setLater((all) => ({ ...all, [f.key]: e.target.checked }))}
                />
                나중에 입력(초안에 빈칸으로 둡니다)
              </label>
            )}
          </div>
        ))}
      </div>
      {error && <p className="issue error">{error}</p>}
      {state === 'answered' ? (
        <p className="muted">답변을 보냈습니다.</p>
      ) : (
        <button type="button" className="primary" onClick={submit} disabled={!complete || disabled}>
          {state === 'sending' ? '보내는 중…' : '입력하고 계속'}
        </button>
      )}
    </section>
  )
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))}KB` : `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

function reviewedLabel(status: DraftStatus) {
  const d = new Date(status.reviewed_at!)
  return `검토 완료 · ${status.reviewed_by} · ${d.getMonth() + 1}/${d.getDate()}`
}

export function DraftCard({ data }: { data: DraftCardData }) {
  const isLawyer = useUser().role === 'lawyer'
  // 카드는 대화에 저장된 그대로이므로 검토 상태는 서버에서 따로 읽는다.
  const [status, setStatus] = useState<DraftStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getDraft(data.draft_id)
      .then(setStatus)
      .catch(() => setStatus(null))
  }, [data.draft_id])

  const [uploading, setUploading] = useState(false)

  // 워드에서 고쳐 실제로 낸 최종본을 올린다. 자료실에는 초안 대신 최종본이 들어간다.
  async function upload(file: File) {
    setError(null)
    setUploading(true)
    try {
      setStatus(await uploadFinal(data.draft_id, file))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  async function review() {
    setError(null)
    try {
      setStatus(await reviewDraft(data.draft_id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const reviewed = status?.reviewed_by != null
  return (
    <section className="card">
      <header className="card-header">
        <h3>초안 · {data.form_name}</h3>
        {reviewed ? (
          <span className="review-badge done">{reviewedLabel(status!)}</span>
        ) : (
          <span className="review-badge">검토 전</span>
        )}
      </header>
      <a className="draft-file" href={downloadUrl(data.file_id)} download={data.filename}>
        <span className="file-icon docx" aria-hidden="true">
          DOCX
        </span>
        <span className="file-text">
          <span className="file-name">{data.filename}</span>
          <span className="file-detail">Word 문서 · {formatSize(data.size)} · 눌러서 내려받기</span>
        </span>
      </a>
      {data.blanks.length > 0 && (
        <p className="issue warning">빈칸으로 둔 항목: {data.blanks.join(', ')}. 제출 전에 채워야 합니다.</p>
      )}
      {data.references && <ReferenceList items={data.references} open={!!data.brief} title={data.brief ? '참고한 문서' : '참고할 과거 서면'} />}
      <dl className="draft-fields">
        {data.fields.map((f) => (
          <div key={f.label}>
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      {data.brief && (
        <details className="references">
          <summary>본문 근거 · 점검 결과 · 판례 후보</summary>
          <BriefView
            draftId={data.draft_id}
            brief={data.brief}
            caseNumber={data.case_number}
            canInsertCitation={isLawyer}
            showReferences={false}
          />
        </details>
      )}
      {status?.final_file_id && (
        <p className="final-line">
          최종본:{' '}
          <a href={downloadUrl(status.final_file_id)} download={status.final_filename ?? undefined}>
            {status.final_filename}
          </a>
          <span className="muted"> · {status.final_uploaded_by} 올림 · 자료실에는 초안 대신 최종본이 들어갑니다</span>
        </p>
      )}
      {error && <p className="issue error">{error}</p>}
      {status && (
        <div className="review-actions">
          {isLawyer && !reviewed && (
            <button type="button" className="secondary small" onClick={review}>
              검토 완료로 표시
            </button>
          )}
          <label className="secondary small file-button">
            {uploading ? '올리는 중…' : status.final_file_id ? '최종본 다시 올리기' : '최종본 올리기'}
            <input
              type="file"
              accept=".docx,.pdf"
              hidden
              disabled={uploading}
              onChange={(e) => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) upload(file)
              }}
            />
          </label>
        </div>
      )}
      <p className="card-foot">
        lawca가 만든 초안입니다. 원문과 대조하고 담당 변호사 검토를 거친 뒤 제출하세요.
        {status?.created_by && ` 작성: ${status.created_by}`}
      </p>
    </section>
  )
}
