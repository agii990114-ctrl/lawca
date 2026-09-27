import { useUser } from './UserContext'

// 사이드바 아래의 로그인한 사용자 표시와 비밀번호 변경·로그아웃.
export default function UserMenu({ onChangePassword, onLogout }: { onChangePassword: () => void; onLogout: () => void }) {
  const user = useUser()
  return (
    <div className="user-menu">
      <div className="user-who">
        <span className="user-name">{user.name}</span>
        <span className={`role-badge ${user.role}`}>{user.role_label}</span>
      </div>
      <div className="user-actions">
        <button type="button" className="link-button" onClick={onChangePassword}>
          비밀번호 변경
        </button>
        <button type="button" className="link-button" onClick={onLogout}>
          로그아웃
        </button>
      </div>
    </div>
  )
}
