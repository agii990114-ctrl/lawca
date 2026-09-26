import { useEffect, useRef, useState } from 'react'
import { streamChat, uploadFile, type ChatEvent } from './api'
import Composer from './chat/Composer'
import MessageView from './chat/MessageView'
import PreviewPanel from './chat/PreviewPanel'
import Sidebar from './chat/Sidebar'
import { newId, type Attachment, type Conversation, type Message, type Preview } from './chat/types'

const EXAMPLES = ['무엇을 할 수 있나요?', '이번 주에 만료되는 기한 알려줘', '확정증명원 신청서 만들어 줘']

const emptyConversation = (): Conversation => ({ id: newId(), title: '새 대화', messages: [] })

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

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>(() => [emptyConversation()])
  const [activeId, setActiveId] = useState(conversations[0].id)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [dragging, setDragging] = useState(false)
  const aborts = useRef(new Map<string, AbortController>())
  const threadEnd = useRef<HTMLDivElement>(null)
  const dragDepth = useRef(0)

  const active = conversations.find((c) => c.id === activeId) ?? conversations[0]
  const streaming = active.messages.some((m) => m.state === 'streaming')

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: 'end' })
  }, [active.messages])

  function updateMessage(conversationId: string, messageId: string, fn: (m: Message) => Message) {
    setConversations((all) =>
      all.map((c) =>
        c.id === conversationId ? { ...c, messages: c.messages.map((m) => (m.id === messageId ? fn(m) : m)) } : c,
      ),
    )
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

    const conversationId = active.id
    const now = new Date().toISOString()
    const user: Message = { id: newId(), role: 'user', text, attachments: ready, steps: [], cards: [], state: 'done', createdAt: now }
    const reply: Message = { id: newId(), role: 'assistant', text: '', attachments: [], steps: [], cards: [], state: 'streaming', createdAt: now }
    setConversations((all) =>
      all.map((c) =>
        c.id === conversationId
          ? {
              ...c,
              title: c.messages.length === 0 ? (text || ready[0].name).slice(0, 40) : c.title,
              messages: [...c.messages, user, reply],
            }
          : c,
      ),
    )
    setDraft('')
    setAttachments((all) => all.filter((a) => a.state === 'uploading'))

    const controller = new AbortController()
    aborts.current.set(reply.id, controller)
    try {
      await streamChat(
        { message: text, file_ids: ready.map((a) => a.id!) },
        (event) => updateMessage(conversationId, reply.id, (m) => applyEvent(m, event)),
        controller.signal,
      )
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
    }
  }

  function stop() {
    for (const m of active.messages) aborts.current.get(m.id)?.abort()
  }

  function newConversation() {
    const existing = conversations.find((c) => c.messages.length === 0)
    if (existing) {
      setActiveId(existing.id)
    } else {
      const c = emptyConversation()
      setConversations((all) => [c, ...all])
      setActiveId(c.id)
    }
    setPreview(null)
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
        conversations={conversations}
        activeId={active.id}
        onSelect={(id) => {
          setActiveId(id)
          setPreview(null)
        }}
        onNew={newConversation}
      />

      <main className="chat">
        {active.messages.length === 0 ? (
          <div className="empty">
            <h1>무엇을 도와드릴까요?</h1>
            <p className="muted">
              법원 문서 PDF를 첨부하면 내용을 읽고 기한과 할 일을 정리합니다. 개발 단계이므로 실제 의뢰인 문서는 올리지
              마세요(Gemini API로 전송됩니다).
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
              {active.messages.map((m) => (
                <MessageView
                  key={m.id}
                  message={m}
                  onOpenFile={(fileId, name, page = 1) => setPreview({ fileId, name, page })}
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
