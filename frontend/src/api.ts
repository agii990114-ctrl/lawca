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
  hearing?: { date: string; time: string | null; kind: string | null; place: string | null; evidence: Evidence } | null
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
  key: string
  label: string
  rule_id: string | null
  period: Period | null
  note: string | null
}

export interface DocumentResult {
  document_id?: string | null
  file_id: string
  filename: string
  model: string
  text_available: boolean
  extraction: CourtDocument
  issues: Issue[]
  suggestions: Suggestion[]
  checklist: string[]
}

export interface CaseSummary {
  case_number: string
  court: string | null
  case_name: string | null
  parties: { role: string; name: string }[]
  documents: { document_type: string; issued_date: string | null; filename: string; file_id: string }[]
  open_deadlines: number
  deadlines?: {
    deadline: string
    weekday: string
    days_left: number
    label: string
    status: string
    served_on: string
  }[]
}

export interface QuestionField {
  key: string
  label: string
  type: 'text' | 'date' | 'select' | 'number' | 'textarea'
  options: string[]
  default: string | null
  allow_later: boolean
  help: string | null
}

export interface QuestionCardData {
  job_id: string
  question_id: string
  stage: 'form' | 'case' | 'fields'
  title: string
  message?: string
  fields: QuestionField[]
  errors: string[]
  references?: SearchCardItem[]
}

export interface DraftCardData {
  draft_id: string
  form_id: string
  form_name: string
  file_id: string
  filename: string
  size: number
  case_number: string | null
  fields: { label: string; value: string }[]
  blanks: string[]
  references?: SearchCardItem[]
  brief?: BriefPayload
}

export const LATER = '__later__'

export type Card =
  | ({ kind: 'document' } & DocumentResult)
  | { kind: 'deadlines'; title: string; items: DeadlineRecord[] }
  | { kind: 'cases'; title: string; items: CaseSummary[] }
  | ({ kind: 'deadline_calc'; label: string } & DeadlineResult)
  | ({ kind: 'question' } & QuestionCardData)
  | ({ kind: 'draft' } & DraftCardData)
  | { kind: 'search'; query: string; items: SearchCardItem[] }
  | ({ kind: 'brief_summary'; document_id: string; document_type: string; file_id: string; filename: string; case_number: string | null } & BriefSummary)

export interface BriefSummary {
  submitter: string | null
  request_summary: string | null
  claims: { point: string; detail: string; quote: string; page: number; verified: boolean }[]
  evidence: { side: EvidenceSide; number: string; label: string; title: string }[]
}

export interface SearchCardItem {
  doc_id: string
  title: string
  kind: LibraryKind
  kind_label: string
  status_label: string | null
  snippet: string
  page: number | null
  file_id: string
  filename: string
  case_number: string | null
  created_at: string
  matched: ('keyword' | 'semantic')[]
  /** 준비서면 초안이 참고한 문서일 때: 참고 번호, 대응한 메모 번호, 본문에 반영했는지 */
  number?: number
  note_no?: number | null
  note_nos?: number[]
  used?: boolean
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

export type ServiceKind = 'electronic_confirmed' | 'electronic_deemed' | 'paper'
export type DeadlineStatus = 'confirmed' | 'done' | 'cancelled'

export const SERVICE_LABELS: Record<ServiceKind, string> = {
  electronic_confirmed: '전자소송에서 확인한 날',
  electronic_deemed: '전자소송 간주 송달일',
  paper: '종이 문서를 받은 날',
}

export interface DeadlineRecord {
  id: string
  status: DeadlineStatus
  key: string
  label: string
  kind: 'statutory' | 'designated'
  rule_id: string | null
  period: Period
  event_date: string
  service_kind: ServiceKind
  service_label: string
  count_start: string
  nominal_end: string
  deadline: string
  extended_over: { day: string; reason: string }[]
  basis: string[]
  warnings: string[]
  created_at: string
  confirmed_by: string
  status_changed_at: string | null
  document_id: string
  document_type: string
  file_id: string
  filename: string
  case_number: string | null
  court: string | null
  case_name: string | null
}

export interface DeadlineConfirmRequest {
  document_id?: string | null
  file_id?: string
  label: string
  event_date: string
  rule_id?: string
  period?: Period
  service_kind: ServiceKind
}

export interface UploadedFile {
  id: string
  name: string
  size: number
  mime: string
  pages: number | null
}

export interface ConversationSummary {
  id: string
  title: string
  updated_at: string
}

export interface ServerMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  attachments: UploadedFile[]
  steps: { id: string; label: string; state: 'running' | 'done' | 'error' }[]
  cards: Card[]
  state: 'streaming' | 'done' | 'error' | 'stopped'
  created_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: ServerMessage[]
}

export type ChatEvent =
  | { type: 'status'; id: string; label: string; state: 'running' | 'done' | 'error' }
  | { type: 'text'; delta: string }
  | { type: 'card'; card: Card }
  | { type: 'done' }

// 로그인이 풀리면(401) 앱이 로그인 화면으로 돌아가도록 알린다.
let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

async function errorMessage(res: Response): Promise<string> {
  if (res.status === 401) onUnauthorized()
  const body = await res.json().catch(() => null)
  return typeof body?.detail === 'string' ? body.detail : `요청이 실패했습니다(${res.status}).`
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(await errorMessage(res))
  return res.json()
}

export interface Health {
  provider: 'gemini' | 'ollama'
  models: string[]
  configured: boolean
}

export const getHealth = () => getJson<Health>('/api/health')

// 로그인·사용자

export type Role = 'clerk' | 'lawyer' | 'admin'

export interface User {
  id: string
  username: string
  name: string
  role: Role
  role_label: string
  created_at: string
  last_login_at: string | null
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

async function send(url: string, init: RequestInit): Promise<void> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(await errorMessage(res))
}

// 로그인 여부 확인. 로그인하지 않았으면 null(로그인 화면으로 보내는 알림은 하지 않는다).
export async function getMe(): Promise<User | null> {
  const res = await fetch('/api/auth/me')
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`서버에 연결하지 못했습니다(${res.status}).`)
  return res.json()
}

export async function login(username: string, password: string): Promise<User> {
  const res = await fetch('/api/auth/login', jsonBody('POST', { username, password }))
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `로그인하지 못했습니다(${res.status}).`)
  }
  return res.json()
}

export const logout = () => send('/api/auth/logout', { method: 'POST' })

export const changePassword = (current_password: string, new_password: string) =>
  send('/api/auth/password', jsonBody('POST', { current_password, new_password }))

export const listUsers = () => getJson<User[]>('/api/users')

export const createUser = (req: { username: string; name: string; role: Role; password: string }) =>
  getJson<User>('/api/users', jsonBody('POST', req))

export const deleteUser = (id: string) => send(`/api/users/${id}`, { method: 'DELETE' })

export const resetPassword = (id: string, password: string) =>
  send(`/api/users/${id}/password`, jsonBody('POST', { password }))

// 서식 초안 검토

export interface DraftStatus {
  id: string
  form_id: string
  created_by: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  final_file_id: string | null
  final_filename: string | null
  final_uploaded_by: string | null
  final_uploaded_at: string | null
}

export function uploadFinal(draftId: string, file: File) {
  const body = new FormData()
  body.append('file', file)
  return getJson<DraftStatus>(`/api/drafts/${draftId}/final`, { method: 'POST', body })
}

export const getDraft = (id: string) => getJson<DraftStatus>(`/api/drafts/${id}`)
export const reviewDraft = (id: string) => getJson<DraftStatus>(`/api/drafts/${id}/review`, { method: 'POST' })

export const listConversations = () => getJson<ConversationSummary[]>('/api/conversations')
export const createConversation = () => getJson<ConversationSummary>('/api/conversations', { method: 'POST' })
export const getConversation = (id: string) => getJson<ConversationDetail>(`/api/conversations/${id}`)

export const confirmDeadline = (req: DeadlineConfirmRequest) =>
  getJson<DeadlineRecord>('/api/deadlines/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })

export function listDeadlines(params: { status?: DeadlineStatus[]; document_id?: string; case_number?: string } = {}) {
  const query = new URLSearchParams()
  if (params.status?.length) query.set('status', params.status.join(','))
  if (params.document_id) query.set('document_id', params.document_id)
  if (params.case_number) query.set('case_number', params.case_number)
  return getJson<DeadlineRecord[]>(`/api/deadlines?${query}`)
}

export const updateDeadlineStatus = (id: string, status: DeadlineStatus) =>
  getJson<DeadlineRecord>(`/api/deadlines/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })

export const DEADLINE_EXPORT = { ics: '/api/deadlines/export.ics', csv: '/api/deadlines/export.csv' }

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
      if (xhr.status === 401) onUnauthorized()
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
async function readEvents(res: Response, onEvent: (event: ChatEvent) => void): Promise<void> {
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

export async function streamChat(
  req: { conversation_id: string; message: string; file_ids: string[] },
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return readEvents(
    await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal,
    }),
    onEvent,
  )
}

export const getJob = (id: string) =>
  getJson<{ id: string; status: string; question: { question_id?: string } | null }>(`/api/jobs/${id}`)

// 되묻기에 답하고 이어지는 답변을 SSE로 받는다.
export async function streamResume(
  jobId: string,
  answers: Record<string, string>,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return readEvents(
    await fetch(`/api/jobs/${jobId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers }),
      signal,
    }),
    onEvent,
  )
}

export const downloadUrl = (id: string) => `/api/files/${id}/content`

export const fileUrl = (id: string, page = 1) => `/api/files/${id}/content#page=${page}`

// 캘린더

export type CalendarSource = 'deadline' | 'hearing' | 'manual'

export interface CalendarItem {
  id: string
  source: CalendarSource
  day: string
  time: string | null
  title: string
  label: string
  status: 'tentative' | 'confirmed' | 'done'
  case_number: string | null
  court: string | null
  case_name: string | null
  location: string | null
  memo: string
  created_by: string | null
  confirmed_by: string | null
  visibility: 'firm' | 'private'
  file_id: string | null
  filename: string | null
  document_type: string | null
  details: string[]
  can_edit: boolean
  document_id: string | null
}

export interface CalendarData {
  start: string
  end: string
  items: CalendarItem[]
  holidays: { day: string; name: string }[]
}

export interface PendingDocument {
  document_id: string
  file_id: string
  filename: string
  document_type: string
  case_number: string | null
  court: string | null
  issued_date: string | null
  created_at: string
  suggestions: Suggestion[]
}

export interface EventInput {
  title: string
  day: string
  time: string | null
  location: string | null
  memo: string
  visibility: 'firm' | 'private'
  case_number: string | null
}

export type EventPatch = Partial<Omit<EventInput, 'visibility' | 'case_number'>> & {
  clear_time?: boolean
  status?: 'confirmed' | 'cancelled'
}

export function getCalendar(start: string, end: string, mine: boolean) {
  const query = new URLSearchParams({ start, end, mine: String(mine) })
  return getJson<CalendarData>(`/api/calendar?${query}`)
}

export const createEvent = (input: EventInput) => getJson<CalendarItem>('/api/events', jsonBody('POST', input))

export const updateEvent = (id: string, patch: EventPatch) =>
  getJson<CalendarItem>(`/api/events/${id}`, jsonBody('PATCH', patch))

export const getPending = () =>
  getJson<{ documents: PendingDocument[]; hearings: CalendarItem[] }>('/api/deadlines/pending')

export interface DocumentDetail {
  document_id: string
  file_id: string
  filename: string
  document_type: string
  model: string
  created_at: string
  case_number: string | null
  court: string | null
  case_name: string | null
  parties: { role: string; name: string }[]
  corrected: boolean
  extraction: CourtDocument
  issues: Issue[]
  suggestions: Suggestion[]
  deadlines: DeadlineRecord[]
  hearings: CalendarItem[]
  pending_dismissed: boolean
}

export const getDocument = (id: string) => getJson<DocumentDetail>(`/api/documents/${id}`)

export const correctDocumentCase = (id: string, body: { case_number: string; court: string | null; case_name: string | null }) =>
  getJson<DocumentDetail>(`/api/documents/${id}/case`, jsonBody('PATCH', body))

export const updateDeadlineTerms = (
  id: string,
  body: { event_date: string; service_kind: ServiceKind; label?: string; period?: Period },
) => getJson<DeadlineRecord>(`/api/deadlines/${id}/terms`, jsonBody('PATCH', body))

export const deleteDeadline = (id: string) => send(`/api/deadlines/${id}`, { method: 'DELETE' })

export const dismissPending = (documentId: string) =>
  send(`/api/documents/${documentId}/dismiss-pending`, { method: 'POST' })

export const issueFeed = () => getJson<{ path: string }>('/api/calendar/feed', { method: 'POST' })

// 자료실

export type LibraryKind = 'filing' | 'form' | 'court' | 'draft' | 'other'

export const LIBRARY_KINDS: { value: LibraryKind; label: string }[] = [
  { value: 'filing', label: '서면' },
  { value: 'form', label: '서식' },
  { value: 'court', label: '법원 문서' },
  { value: 'draft', label: 'lawca 초안' },
  { value: 'other', label: '기타' },
]

export interface LibraryDoc {
  id: string
  title: string
  kind: LibraryKind
  kind_label: string
  file_id: string
  filename: string
  mime: string
  case_number: string | null
  created_by: string
  created_at: string
  chunk_count: number
  embedded: boolean
  can_delete: boolean
  status_label: string | null
  draft_id: string | null
}

export interface LibrarySearch {
  query: string
  semantic: boolean
  hits: { doc: LibraryDoc; snippet: string; page: number | null; matched: ('keyword' | 'semantic')[] }[]
}

export function listLibrary(kind?: LibraryKind) {
  return getJson<LibraryDoc[]>(`/api/library${kind ? `?kind=${kind}` : ''}`)
}

export function searchLibrary(q: string, kind?: LibraryKind) {
  const query = new URLSearchParams({ q })
  if (kind) query.set('kind', kind)
  return getJson<LibrarySearch>(`/api/library/search?${query}`)
}

export function uploadLibrary(file: File, form: { kind: LibraryKind; title: string; case_number: string }) {
  const body = new FormData()
  body.append('file', file)
  body.append('kind', form.kind)
  if (form.title.trim()) body.append('title', form.title.trim())
  if (form.case_number.trim()) body.append('case_number', form.case_number.trim())
  return getJson<LibraryDoc>('/api/library', { method: 'POST', body })
}

export const deleteLibrary = (id: string) => send(`/api/library/${id}`, { method: 'DELETE' })
export const reindexLibrary = (id: string) => getJson<LibraryDoc>(`/api/library/${id}/reindex`, { method: 'POST' })

// 사건·증거·준비서면

export type EvidenceSide = '갑' | '을' | '병'

export interface CaseListItem {
  case_number: string
  court: string | null
  case_name: string | null
  parties: { role: string; name: string }[]
  documents: number
  open_deadlines: number
  evidence: number
}

export interface EvidenceItem {
  id: string
  side: EvidenceSide
  number: string
  label: string
  title: string
  note: string
  submitted_on: string | null
  from_summary: boolean
  created_by: string
}

export interface CaseDetail extends CaseListItem {
  facts: Record<string, string>
  document_list: {
    document_id: string
    document_type: string
    issued_date: string | null
    filename: string
    file_id: string
    created_at: string
    summary: BriefSummary | null
  }[]
  deadlines: DeadlineRecord[]
  evidence_list: EvidenceItem[]
}

const caseUrl = (n: string) => `/api/cases/${encodeURIComponent(n)}`

export const listCases = (q?: string) => getJson<CaseListItem[]>(`/api/cases${q ? `?q=${encodeURIComponent(q)}` : ''}`)
export const getCase = (n: string) => getJson<CaseDetail>(caseUrl(n))

export const addEvidence = (n: string, body: { side: EvidenceSide; number?: string; title: string; note?: string }) =>
  getJson<EvidenceItem>(`${caseUrl(n)}/evidence`, jsonBody('POST', body))

export const addEvidenceBulk = (n: string, source_document_id: string, items: { side: string; number: string; title: string }[]) =>
  getJson<{ added: string[]; skipped: string[] }>(`${caseUrl(n)}/evidence/bulk`, jsonBody('POST', { source_document_id, items }))

export const updateEvidence = (
  id: string,
  body: { number?: string; title?: string; note?: string; submitted_on?: string; clear_submitted?: boolean },
) => getJson<EvidenceItem>(`/api/evidence/${id}`, jsonBody('PATCH', body))

export const deleteEvidence = (id: string) => send(`/api/evidence/${id}`, { method: 'DELETE' })

export interface CitationCandidates {
  query: string
  items: {
    id: string
    citation: string
    case_number: string
    case_name: string
    date: string
    holdings: string
    summary: string
    references: string[]
    url: string
  }[]
  statutes: { reference: string; url: string | null }[]
}

export const findCitations = (n: string, body: { issue: string; keywords: string[] }) =>
  getJson<CitationCandidates>(`${caseUrl(n)}/citations`, jsonBody('POST', body))

// 초안(서식·준비서면)

export interface BriefChecks {
  notes_tracked: boolean
  unused_notes: { number: number; text: string }[]
  case_law: string[]
  statutes_not_in_notes: string[]
  unknown_evidence: string[]
  amounts_not_in_inputs: string[]
  dates_not_in_inputs: string[]
  paragraphs_without_source: number
  placeholders: number
  references_used: number[]
  foreign_names: string[]
}

export interface BriefPayload {
  sections: { heading: string; paragraphs: { text: string; sources: string[] }[] }[]
  open_points: string[]
  citation_needs: { issue: string; keywords: string[] }[]
  checks: BriefChecks | null
  references: SearchCardItem[]
  opponent_document: string | null
  model: string | null
  note: string | null
  filled: Record<string, string>
  evidence: string[]
}

export interface DraftListItem {
  id: string
  form_id: string
  form_name: string
  case_number: string | null
  case_name: string | null
  filename: string
  file_id: string
  size: number
  created_by: string | null
  created_at: string
  reviewed_by: string | null
  reviewed_at: string | null
  final_file_id: string | null
  final_filename: string | null
  status_label: '최종본' | '검토 완료' | '검토 전'
  blanks: string[]
}

export interface DraftDetail extends DraftListItem {
  fields: { label: string; value: string }[]
  brief: BriefPayload | null
}

export function listDrafts(params: { case_number?: string; form_id?: string } = {}) {
  const query = new URLSearchParams()
  if (params.case_number) query.set('case_number', params.case_number)
  if (params.form_id) query.set('form_id', params.form_id)
  return getJson<DraftListItem[]>(`/api/drafts?${query}`)
}

export const getDraftDetail = (id: string) => getJson<DraftDetail>(`/api/drafts/${id}/detail`)

export interface PrefilingItem {
  level: 'error' | 'warn' | 'ok' | 'info'
  text: string
}

export interface PrefilingCheck {
  items: PrefilingItem[]
  ready: boolean
  checked: string
}

export const getDraftCheck = (id: string) => getJson<PrefilingCheck>(`/api/drafts/${id}/check`)

export const insertCitation = (id: string, index: number, citation: string) =>
  getJson<DraftDetail>(`/api/drafts/${id}/citation`, jsonBody('POST', { index, citation }))
