// 백엔드(lawca.api) 요청·응답 형식. 백엔드를 바꾸면 여기도 함께 고친다.

export type Unit = '일' | '주' | '월' | '년'

export interface Evidence {
  quote: string
  page: number
}

export interface TextField {
  value: string
  evidence: Evidence
}

export interface Party {
  role: string
  name: string
  evidence: Evidence
}

export interface CourtDocument {
  document_type: string
  document_type_evidence: Evidence
  court: TextField | null
  case_number: TextField | null
  case_name: TextField | null
  parties: Party[]
  issued_date: TextField | null
  order_summary: TextField | null
  designated_period: { amount: number; unit: Unit; evidence: Evidence } | null
}

export interface Period {
  amount: number
  unit: Unit
  label: string
}

export interface Issue {
  field: string
  level: 'error' | 'warning'
  message: string
}

export interface Suggestion {
  kind: 'statutory' | 'designated'
  label: string
  rule_id: string | null
  period: Period | null
  note: string | null
}

export interface DocumentResult {
  file_id: string
  filename: string
  model: string
  text_available: boolean
  extraction: CourtDocument
  issues: Issue[]
  suggestions: Suggestion[]
  checklist: string[]
}

export type Card = { kind: 'document' } & DocumentResult

export interface DeadlineResult {
  event_date: string
  period: Period
  count_start: string
  nominal_end: string
  deadline: string
  extended_over: { day: string; reason: string }[]
  basis: string[]
  warnings: string[]
}

export interface DeadlineRequest {
  event_date: string
  rule_id?: string
  period?: Period
  deemed_electronic_service: boolean
}

export interface UploadedFile {
  id: string
  name: string
  size: number
  mime: string
  pages: number | null
}

export type ChatEvent =
  | { type: 'status'; id: string; label: string; state: 'running' | 'done' | 'error' }
  | { type: 'text'; delta: string }
  | { type: 'card'; card: Card }
  | { type: 'done' }

async function errorMessage(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  return typeof body?.detail === 'string' ? body.detail : `요청이 실패했습니다(${res.status}).`
}

export async function computeDeadline(req: DeadlineRequest): Promise<DeadlineResult> {
  const res = await fetch('/api/deadlines', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  return res.json()
}

// 진행률을 받으려고 fetch 대신 XMLHttpRequest를 쓴다.
export function uploadFile(file: File, onProgress: (ratio: number) => void): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/files')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onload = () => {
      const body = (() => {
        try {
          return JSON.parse(xhr.responseText)
        } catch {
          return null
        }
      })()
      if (xhr.status >= 200 && xhr.status < 300) resolve(body)
      else reject(new Error(typeof body?.detail === 'string' ? body.detail : `업로드에 실패했습니다(${xhr.status}).`))
    }
    xhr.onerror = () => reject(new Error('서버에 연결하지 못했습니다.'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

// 서버가 보내는 SSE(data: {...}\n\n)를 읽어 이벤트마다 onEvent를 부른다.
export async function streamChat(
  req: { message: string; file_ids: string[] },
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(await errorMessage(res))
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)))
      }
    }
  }
}

export const fileUrl = (id: string, page = 1) => `/api/files/${id}/content#page=${page}`
