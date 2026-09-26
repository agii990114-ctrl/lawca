import { useEffect, useId, useState } from 'react'
import { downloadUrl, getJob, LATER, type DraftCardData, type QuestionCardData, type QuestionField } from '../api'

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

export function DraftCard({ data }: { data: DraftCardData }) {
  return (
    <section className="card">
      <header className="card-header">
        <h3>초안 · {data.form_name}</h3>
        <span className="muted">검토 필요</span>
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
      <dl className="draft-fields">
        {data.fields.map((f) => (
          <div key={f.label}>
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className="card-foot">lawca가 만든 초안입니다. 원문과 대조하고 담당 변호사 검토를 거친 뒤 제출하세요.</p>
    </section>
  )
}
