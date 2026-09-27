import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  createConversation,
  getConversation,
  getHealth,
  listConversations,
  streamChat,
  streamResume,
  uploadFile,
  type ChatEvent,
  type ConversationSummary,
  type Health,
} from './api'
import Composer from './chat/Composer'
import CalendarView from './calendar/CalendarView'
import DeadlinesView from './DeadlinesView'
import LibraryView from './library/LibraryView'
import CasesView from './cases/CasesView'
import MessageView from './chat/MessageView'
import PreviewPanel from './chat/PreviewPanel'
import Sidebar from './chat/Sidebar'
import { fromServer, newId, type Attachment, type Message, type Preview } from './chat/types'

type View = 'chat' | 'deadlines' | 'calendar' | 'library' | 'cases'

const EXAMPLES = ['이번 주에 만료되는 기한 알려줘', '예전에 쓴 주소보정서 찾아줘', '9월 15일에 판결문을 받았으면 항소기한은?']
const NO_MESSAGES: Message[] = []

function applyEvent(message: Message, event: ChatEvent): Message {
  switch (event.type) {
    case 'status': {
      const exists = message.steps.some((s) => s.id === event.id)
      const step = { id: event.id, label: event.label, state: event.state }
      return {
        ...message,
        steps: exists ? message.steps.map((s) => (s.id === event.id ? step : s)) : [...message.steps, step],
      }
    }
    case 'text':
      return { ...message, text: message.text + event.delta }
    case 'card':
      return { ...message, cards: [...message.cards, event.card] }
    case 'done':
      return { ...message, state: 'done' }
  }
}

export default function App({ userMenu }: { userMenu: ReactNode }) {
  const [summaries, setSummaries] = useState<ConversationSummary[]>([])
  const [threads, setThreads] = useState<Record<string, Message[]>>({})
  // null이면 아직 저장하지 않은 새 대화. 첫 메시지를 보낼 때 서버에 만든다.
  const [activeId, setActiveId] = useState<string | null>(null)
  const [view, setView] = useState<View>('chat')
  // 사건 화면에서 먼저 열 사건(채팅 카드의 '사건 화면에서 보기')
  const [caseToOpen, setCaseToOpen] = useState<string | null>(null)
  // 좁은 화면에서만 쓰는 사이드바 열림 상태
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [threadError, setThreadError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [dragging, setDragging] = useState(false)
  const aborts = useRef(new Map<string, AbortController>())
  const threadEnd = useRef<HTMLDivElement>(null)
  const dragDepth = useRef(0)

  const messages = (activeId && threads[activeId]) || NO_MESSAGES
  const streaming = messages.some((m) => m.state === 'streaming')

  const refreshList = useCallback(() => {
    listConversations()
      .then((list) => {
        setSummaries(list)
        setLoadError(null)
      })
      .catch((e: Error) => setLoadError(`대화 목록을 불러오지 못했습니다. ${e.message}`))
  }, [])

  useEffect(refreshList, [refreshList])

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  async function openConversation(id: string) {
    setSidebarOpen(false)
    setView('chat')
    setActiveId(id)
    setPreview(null)
    setThreadError(null)
    if (threads[id]) return
    try {
      const detail = await getConversation(id)
      setThreads((all) => ({ ...all, [id]: detail.messages.map(fromServer) }))
    } catch (e) {
      setThreadError(e instanceof Error ? e.message : String(e))
    }
  }

  function updateMessage(conversationId: string, messageId: string, fn: (m: Message) => Message) {
    setThreads((all) => ({
      ...all,
      [conversationId]: (all[conversationId] ?? []).map((m) => (m.id === messageId ? fn(m) : m)),
    }))
  }

  function patchAttachment(localId: string, patch: Partial<Attachment>) {
    setAttachments((all) => all.map((a) => (a.localId === localId ? { ...a, ...patch } : a)))
  }

  function addFiles(files: File[]) {
    for (const file of files) {
      const localId = newId()
      const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
      setAttachments((all) => [
        ...all,
        {
          localId,
          name: file.name,
          size: file.size,
          state: isPdf ? 'uploading' : 'error',
          progress: 0,
          error: isPdf ? undefined : '지금은 PDF만 올릴 수 있습니다',
        },
      ])
      if (!isPdf) continue
      uploadFile(file, (progress) => patchAttachment(localId, { progress }))
        .then((uploaded) => patchAttachment(localId, { state: 'ready', id: uploaded.id, pages: uploaded.pages }))
        .catch((e: Error) => patchAttachment(localId, { state: 'error', error: e.message }))
    }
  }

  async function send(textOverride?: string) {
    const text = (textOverride ?? draft).trim()
    const ready = attachments.filter((a) => a.state === 'ready')
    if (streaming || (!text && ready.length === 0)) return

    let conversationId = activeId
    if (!conversationId) {
      try {
        const created = await createConversation()
        conversationId = created.id
        setSummaries((all) => [created, ...all])
        setThreads((all) => ({ ...all, [created.id]: [] }))
        setActiveId(created.id)
      } catch (e) {
        setLoadError(`대화를 만들지 못했습니다. ${e instanceof Error ? e.message : String(e)}`)
        return
      }
    }

    const target = conversationId
    setDraft('')
    await converse(target, text, ready, (onEvent, signal) =>
      streamChat({ conversation_id: target, message: text, file_ids: ready.map((a) => a.id!) }, onEvent, signal),
    )
  }

  // 사용자 메시지와 답변 자리를 대화에 붙이고, 답변 이벤트를 받아 채운다.
  async function converse(
    conversationId: string,
    text: string,
    files: Attachment[],
    open: (onEvent: (event: ChatEvent) => void, signal: AbortSignal) => Promise<void>,
  ) {
    const now = new Date().toISOString()
    const user: Message = { id: newId(), role: 'user', text, attachments: files, steps: [], cards: [], state: 'done', createdAt: now }
    const reply: Message = { id: newId(), role: 'assistant', text: '', attachments: [], steps: [], cards: [], state: 'streaming', createdAt: now }
    setThreads((all) => ({ ...all, [conversationId]: [...(all[conversationId] ?? []), user, reply] }))
    setAttachments((all) => all.filter((a) => a.state === 'uploading'))

    const controller = new AbortController()
    aborts.current.set(reply.id, controller)
    try {
      await open((event) => updateMessage(conversationId, reply.id, (m) => applyEvent(m, event)), controller.signal)
      updateMessage(conversationId, reply.id, (m) => (m.state === 'streaming' ? { ...m, state: 'done' } : m))
    } catch (e) {
      const stopped = controller.signal.aborted
      updateMessage(conversationId, reply.id, (m) => ({
        ...m,
        state: stopped ? 'stopped' : 'error',
        steps: m.steps.map((s) => (s.state === 'running' ? { ...s, state: stopped ? 'done' : 'error' } : s)),
        text: stopped || m.text ? m.text : e instanceof Error ? e.message : String(e),
      }))
    } finally {
      aborts.current.delete(reply.id)
      refreshList()
    }
  }

  // 되묻기에 답한다. 답은 사용자 메시지로 남고 이어지는 답변이 흘러나온다.
  async function answer(jobId: string, answers: Record<string, string>, summary: string) {
    if (!activeId || streaming) throw new Error('응답이 끝난 뒤에 입력해 주세요.')
    await converse(activeId, summary, [], (onEvent, signal) => streamResume(jobId, answers, onEvent, signal))
  }

  function stop() {
    for (const m of messages) aborts.current.get(m.id)?.abort()
  }

  function newConversation() {
    setSidebarOpen(false)
    setView('chat')
    setActiveId(null)
    setPreview(null)
    setThreadError(null)
  }

  const composer = (
    <Composer
      text={draft}
      onTextChange={setDraft}
      attachments={attachments}
      onAddFiles={addFiles}
      onRemoveAttachment={(localId) => setAttachments((all) => all.filter((a) => a.localId !== localId))}
      onOpenAttachment={(a) => a.id && setPreview({ fileId: a.id, name: a.name, page: 1 })}
      onSend={() => send()}
      onStop={stop}
      streaming={streaming}
      autoFocus
    />
  )

  return (
    <div
      className={`layout${preview ? ' with-preview' : ''}`}
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return
        dragDepth.current += 1
        setDragging(true)
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1)
        if (dragDepth.current === 0) setDragging(false)
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault()
        dragDepth.current = 0
        setDragging(false)
        addFiles(Array.from(e.dataTransfer.files))
      }}
    >
      <Sidebar
        conversations={summaries}
        activeId={activeId}
        loadError={loadError}
        view={view}
        onSelect={openConversation}
        onNew={newConversation}
        open={sidebarOpen}
        footer={userMenu}
        onShowView={(next) => {
          setSidebarOpen(false)
          setView(next)
          setPreview(null)
        }}
      />
      {sidebarOpen && <div className="scrim" onClick={() => setSidebarOpen(false)} />}

      <main className="chat">
        <div className="mobile-bar">
          <button type="button" className="icon-button" onClick={() => setSidebarOpen(true)} aria-label="메뉴 열기">
            ☰
          </button>
          <span className="brand-inline">lawca</span>
          <span className="mobile-title">
            {view === 'deadlines'
              ? '기한'
              : view === 'calendar'
                ? '캘린더'
                : view === 'library'
                  ? '자료실'
                  : view === 'cases'
                    ? '사건'
                : (summaries.find((c) => c.id === activeId)?.title ?? '새 대화')}
          </span>
        </div>
        {view === 'deadlines' ? (
          <DeadlinesView />
        ) : view === 'calendar' ? (
          <CalendarView onOpenFile={(fileId, name) => setPreview({ fileId, name, page: 1 })} />
        ) : view === 'library' ? (
          <LibraryView />
        ) : view === 'cases' ? (
          <CasesView key={caseToOpen ?? 'cases'} initialCase={caseToOpen} onOpenFile={(fileId, name) => setPreview({ fileId, name, page: 1 })} />
        ) : activeId === null ? (
          <div className="empty">
            <h1>무엇을 도와드릴까요?</h1>
            <p className="muted">
              법원 문서 PDF를 첨부하면 내용을 읽고 기한과 할 일을 정리합니다.{' '}
              {health?.provider === 'ollama'
                ? `문서는 이 PC의 로컬 모델(${health.models[0]})로 처리되어 외부로 전송되지 않습니다. 로컬 모델이라 응답에 1분 넘게 걸릴 수 있습니다.`
                : '개발 단계이므로 실제 의뢰인 문서는 올리지 마세요(Gemini API로 전송됩니다).'}
            </p>
            {composer}
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" onClick={() => send(example)}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="thread">
              {threadError && <p className="issue error">대화를 불러오지 못했습니다. {threadError}</p>}
              {messages.map((m) => (
                <MessageView
                  key={m.id}
                  message={m}
                  onOpenFile={(fileId, name, page = 1) => setPreview({ fileId, name, page })}
                  onShowDeadlines={() => {
                    setView('deadlines')
                    setPreview(null)
                  }}
                  onShowLibrary={() => {
                    setView('library')
                    setPreview(null)
                  }}
                  onShowCase={(caseNumber) => {
                    setCaseToOpen(caseNumber)
                    setView('cases')
                    setPreview(null)
                  }}
                  onAnswer={answer}
                />
              ))}
              <div ref={threadEnd} />
            </div>
            <div className="composer-dock">
              {composer}
              <p className="disclaimer">lawca의 결과는 초안입니다. 기한과 추출값은 확정 전에 원문과 대조하세요.</p>
            </div>
          </>
        )}
      </main>

      {preview && <PreviewPanel preview={preview} onClose={() => setPreview(null)} />}

      {dragging && (
        <div className="drop-overlay">
          <div>여기에 PDF를 놓으세요</div>
        </div>
      )}
    </div>
  )
}
