import type { Card } from '../api'

// 입력창에 붙인 파일. 붙이는 즉시 업로드하고, 끝나면 서버의 id를 받는다.
export interface Attachment {
  localId: string
  name: string
  size: number
  state: 'uploading' | 'ready' | 'error'
  progress: number
  id?: string
  pages?: number | null
  error?: string
}

export interface Step {
  id: string
  label: string
  state: 'running' | 'done' | 'error'
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  attachments: Attachment[]
  steps: Step[]
  cards: Card[]
  state: 'streaming' | 'done' | 'error' | 'stopped'
  createdAt: string
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
}

export interface Preview {
  fileId: string
  name: string
  page: number
}

export const newId = () => crypto.randomUUID()
