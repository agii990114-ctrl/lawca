// 백엔드(lawca.api.app) 응답 형식. 백엔드를 바꾸면 여기도 함께 고친다.

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
  id: string
  filename: string
  model: string
  text_available: boolean
  extraction: CourtDocument
  issues: Issue[]
  suggestions: Suggestion[]
  checklist: string[]
}

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

async function request<T>(url: string, init: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : `요청이 실패했습니다(${res.status}).`
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export function uploadDocument(file: File): Promise<DocumentResult> {
  const form = new FormData()
  form.append('file', file)
  return request('/api/documents', { method: 'POST', body: form })
}

export function computeDeadline(req: DeadlineRequest): Promise<DeadlineResult> {
  return request('/api/deadlines', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

export const pdfUrl = (id: string, page = 1) => `/api/documents/${id}/pdf#page=${page}`
