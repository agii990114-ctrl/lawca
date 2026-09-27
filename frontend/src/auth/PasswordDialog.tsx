import { useState, type FormEvent } from 'react'
import { changePassword } from '../api'

// 본인 비밀번호 변경. 바꾸면 서버가 모든 로그인을 끊으므로 다시 로그인해야 한다.
export default function PasswordDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (next !== confirm) {
      setError('새 비밀번호가 서로 다릅니다.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await changePassword(current, next)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form className="modal" onSubmit={submit} onClick={(e) => e.stopPropagation()} aria-label="비밀번호 변경">
        <h2>비밀번호 변경</h2>
        <label>
          현재 비밀번호
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" autoFocus required />
        </label>
        <label>
          새 비밀번호
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required />
          <span className="muted">8자 이상, 글자와 숫자를 함께 넣어 주세요.</span>
        </label>
        <label>
          새 비밀번호 확인
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
        </label>
        {error && <p className="issue error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            닫기
          </button>
          <button type="submit" className="primary" disabled={busy}>
            바꾸기
          </button>
        </div>
      </form>
    </div>
  )
}
