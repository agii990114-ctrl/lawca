import { useState, type FormEvent } from 'react'
import { login, type User } from '../api'

export default function LoginPage({ onLogin, notice }: { onLogin: (user: User) => void; notice?: string | null }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      onLogin(await login(username.trim(), password))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="brand">lawca</div>
        <p className="muted">법무법인 송무 도우미. 관리자가 만들어 준 아이디로 로그인하세요.</p>
        {notice && !error && <p className="issue warning">{notice}</p>}
        <label>
          아이디
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label>
          비밀번호
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <p className="issue error">{error}</p>}
        <button type="submit" className="primary" disabled={busy || !username.trim() || !password}>
          {busy ? '로그인 중…' : '로그인'}
        </button>
        <p className="muted small-note">비밀번호를 잊었으면 관리자에게 초기화를 요청하세요.</p>
      </form>
    </div>
  )
}
