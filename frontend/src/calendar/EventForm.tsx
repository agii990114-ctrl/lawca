import { useState, type FormEvent } from 'react'
import type { CalendarItem, EventInput } from '../api'

// 일정 추가·수정 창. 수정할 때는 공개 범위와 사건은 바꾸지 않는다.
export default function EventForm({
  initialDay,
  editing,
  onSubmit,
  onClose,
}: {
  initialDay: string
  editing?: CalendarItem
  onSubmit: (input: EventInput) => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<EventInput>({
    title: editing?.label ?? '',
    day: editing?.day ?? initialDay,
    time: editing?.time ?? null,
    location: editing?.location ?? '',
    memo: editing?.memo ?? '',
    visibility: editing?.visibility ?? 'firm',
    case_number: editing?.case_number ?? '',
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSubmit({ ...form, time: form.time || null, case_number: form.case_number?.trim() || null })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form className="modal" onSubmit={submit} onClick={(e) => e.stopPropagation()} aria-label="일정">
        <h2>{editing ? '일정 수정' : '일정 추가'}</h2>
        <label>
          제목
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="예: 의뢰인 상담" autoFocus required />
        </label>
        <div className="form-row">
          <label>
            날짜
            <input type="date" value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value })} required />
          </label>
          <label>
            시각(선택)
            <input type="time" value={form.time ?? ''} onChange={(e) => setForm({ ...form, time: e.target.value || null })} />
          </label>
        </div>
        <label>
          장소(선택)
          <input value={form.location ?? ''} onChange={(e) => setForm({ ...form, location: e.target.value })} />
        </label>
        {!editing && (
          <label>
            사건번호(선택)
            <input value={form.case_number ?? ''} onChange={(e) => setForm({ ...form, case_number: e.target.value })} placeholder="예: 2026가단51234" />
          </label>
        )}
        <label>
          메모(선택)
          <textarea rows={2} value={form.memo} onChange={(e) => setForm({ ...form, memo: e.target.value })} />
        </label>
        {!editing && (
          <fieldset className="visibility">
            <legend>공개 범위</legend>
            <label>
              <input type="radio" checked={form.visibility === 'firm'} onChange={() => setForm({ ...form, visibility: 'firm' })} /> 법인 전체
            </label>
            <label>
              <input type="radio" checked={form.visibility === 'private'} onChange={() => setForm({ ...form, visibility: 'private' })} /> 나만 보기
            </label>
          </fieldset>
        )}
        {error && <p className="issue error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            닫기
          </button>
          <button type="submit" className="primary" disabled={busy}>
            저장
          </button>
        </div>
      </form>
    </div>
  )
}
