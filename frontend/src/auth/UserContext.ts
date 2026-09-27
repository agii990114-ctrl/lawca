import { createContext, useContext } from 'react'
import type { User } from '../api'

// 로그인한 사용자. 로그인한 뒤에만 화면을 그리므로 안에서는 항상 값이 있다.
export const UserContext = createContext<User | null>(null)

export function useUser(): User {
  const user = useContext(UserContext)
  if (!user) throw new Error('로그인한 사용자가 없습니다.')
  return user
}
