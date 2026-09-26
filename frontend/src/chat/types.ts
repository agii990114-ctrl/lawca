import type { Card, ServerMessage } from '../api'

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

export interface Preview {
  fileId: string
  name: string
  page: number
}

export const newId = () => crypto.randomUUID()

// 서버에 저장된 메시지를 화면용 메시지로 바꾼다.
export function fromServer(m: ServerMessage): Message {
  return {
    id: m.id,
    role: m.role,
    text: m.text,
    attachments: m.attachments.map((f) => ({
      localId: f.id,
      name: f.name,
      size: f.size,
      state: 'ready',
      progress: 1,
      id: f.id,
      pages: f.pages,
    })),
    steps: m.steps,
    cards: m.cards,
    state: m.state,
    createdAt: m.created_at,
  }
}
