import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createUser, deleteUser, listUsers, resetPassword, type Role, type User } from '../api'
import { useUser } from '../auth/UserContext'

const ROLES: { value: Role; label: string; note: string }[] = [
  { value: 'clerk', label: '사무원', note: '문서 처리, 기한 확정, 서식 초안, 조회' },
  { value: 'lawyer', label: '변호사', note: '사무원 기능 + 기한 취소·되돌리기, 초안 검토 완료' },
  { value: 'admin', label: '관리자', note: '회원 관리만' },
]

const EMPTY = { username: '', name: '', role: 'clerk' as Role, password: '' }

function formatDateTime(value: string | null) {
  if (!value) return '—'
  const d = new Date(value)
  return `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}. ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function UsersView() {
  const me = useUser()
  const [users, setUsers] = useState<User[] | null>(null)
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 비밀번호를 초기화하는 중인 사용자와 입력값
  const [resetting, setResetting] = useState<{ id: string; password: string } | null>(null)

  const load = useCallback(() => {
    listUsers()
      .then(setUsers)
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(load, [load])

  async function add(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const created = await createUser({ ...form, username: form.username.trim(), name: form.name.trim() })
      setNotice(`${created.name}(${created.username}) 계정을 만들었습니다. 처음 로그인한 뒤 비밀번호를 바꾸도록 안내하세요.`)
      setForm(EMPTY)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(user: User) {
    if (!window.confirm(`${user.name}(${user.username}) 계정을 삭제할까요? 삭제하면 바로 로그인이 끊기고 다시 로그인할 수 없습니다.`)) return
    setError(null)
    setNotice(null)
    try {
      await deleteUser(user.id)
      setNotice(`${user.name}(${user.username}) 계정을 삭제했습니다.`)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function reset(e: FormEvent, user: User) {
    e.preventDefault()
    if (!resetting) return
    setError(null)
    setNotice(null)
    try {
      await resetPassword(user.id, resetting.password)
      setNotice(`${user.name}(${user.username})의 비밀번호를 초기화했습니다. 새 비밀번호를 본인에게 직접 전해 주세요.`)
      setResetting(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="users-view">
      <header className="view-header">
        <div>
          <h1>회원 관리</h1>
          <p className="muted">사원 계정을 추가하고 삭제합니다. 권한에 따라 쓸 수 있는 기능이 다릅니다.</p>
        </div>
      </header>

      <form className="card user-form" onSubmit={add}>
        <h3>계정 추가</h3>
        <div className="user-form-grid">
          <label>
            아이디
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              placeholder="영문·숫자 (예: kim.clerk)"
              pattern="[A-Za-z0-9._\-]{3,50}"
              title="영문, 숫자, . _ - 로 3~50자"
              autoComplete="off"
              required
            />
          </label>
          <label>
            이름
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="홍길동" required />
          </label>
          <label>
            권한
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            처음 비밀번호
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              autoComplete="new-password"
              placeholder="8자 이상, 글자+숫자"
              required
            />
          </label>
        </div>
        <p className="muted">{ROLES.find((r) => r.value === form.role)?.note}</p>
        <button type="submit" className="primary" disabled={busy}>
          추가
        </button>
      </form>

      {notice && <p className="issue ok">{notice}</p>}
      {error && <p className="issue error">{error}</p>}

      {users === null ? (
        <p className="muted">불러오는 중…</p>
      ) : (
        <div className="table-scroll">
          <table className="users-table">
            <thead>
              <tr>
                <th>아이디</th>
                <th>이름</th>
                <th>권한</th>
                <th>만든 날</th>
                <th>마지막 로그인</th>
                <th aria-label="작업" />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.username}</td>
                  <td>{u.name}</td>
                  <td>
                    <span className={`role-badge ${u.role}`}>{u.role_label}</span>
                  </td>
                  <td>{formatDateTime(u.created_at)}</td>
                  <td>{formatDateTime(u.last_login_at)}</td>
                  <td className="actions">
                    {resetting?.id === u.id ? (
                      <form className="inline-reset" onSubmit={(e) => reset(e, u)}>
                        <input
                          type="password"
                          value={resetting.password}
                          onChange={(e) => setResetting({ id: u.id, password: e.target.value })}
                          placeholder="새 비밀번호"
                          autoComplete="new-password"
                          autoFocus
                          required
                        />
                        <button type="submit" className="secondary small">
                          저장
                        </button>
                        <button type="button" className="link-button" onClick={() => setResetting(null)}>
                          취소
                        </button>
                      </form>
                    ) : (
                      <>
                        <button type="button" className="link-button" onClick={() => setResetting({ id: u.id, password: '' })}>
                          비밀번호 초기화
                        </button>
                        {u.id !== me.id && (
                          <button type="button" className="link-button danger" onClick={() => remove(u)}>
                            삭제
                          </button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
