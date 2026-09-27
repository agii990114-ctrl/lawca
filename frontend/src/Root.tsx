import { useEffect, useState } from 'react'
import { getMe, logout, setUnauthorizedHandler, type User } from './api'
import App from './App'
import UsersView from './admin/UsersView'
import LoginPage from './auth/LoginPage'
import PasswordDialog from './auth/PasswordDialog'
import UserMenu from './auth/UserMenu'
import { UserContext } from './auth/UserContext'

// 로그인 상태에 따라 화면을 고른다. 관리자는 회원 관리, 사무원·변호사는 업무 화면.
export default function Root() {
  // undefined: 확인 중, null: 로그인 전
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [notice, setNotice] = useState<string | null>(null)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser((current) => {
        if (current) setNotice('로그인이 만료되었습니다. 다시 로그인하세요.')
        return null
      })
    })
    getMe()
      .then(setUser)
      .catch((e: Error) => setLoadError(e.message))
  }, [])

  async function signOut() {
    await logout().catch(() => undefined)
    setNotice(null)
    setUser(null)
  }

  if (loadError) return <p className="issue error boot-error">{loadError} 백엔드가 실행 중인지 확인하세요.</p>
  if (user === undefined) return null
  if (user === null)
    return (
      <LoginPage
        notice={notice}
        onLogin={(u) => {
          setNotice(null)
          setUser(u)
        }}
      />
    )

  const menu = <UserMenu onChangePassword={() => setPasswordOpen(true)} onLogout={signOut} />
  return (
    <UserContext.Provider value={user}>
      {/* 사용자가 바뀌면 화면 상태(대화 등)를 새로 만든다 */}
      {user.role === 'admin' ? (
        <div className="layout admin-layout" key={user.id}>
          <nav className="sidebar" aria-label="관리">
            <div className="brand">lawca</div>
            <button type="button" className="nav-item active">
              회원 관리
            </button>
            <div className="sidebar-spacer" />
            {menu}
          </nav>
          <main className="chat admin-main">
            <UsersView />
          </main>
        </div>
      ) : (
        <App key={user.id} userMenu={menu} />
      )}
      {passwordOpen && (
        <PasswordDialog
          onClose={() => setPasswordOpen(false)}
          onChanged={() => {
            setPasswordOpen(false)
            setUser(null)
            setNotice('비밀번호를 바꿨습니다. 새 비밀번호로 다시 로그인하세요.')
          }}
        />
      )}
    </UserContext.Provider>
  )
}
