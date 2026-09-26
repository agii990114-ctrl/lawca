import { useEffect, useRef, type ClipboardEvent, type KeyboardEvent } from 'react'
import FileCard from './FileCard'
import type { Attachment } from './types'

export default function Composer({
  text,
  onTextChange,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  onOpenAttachment,
  onSend,
  onStop,
  streaming,
  autoFocus,
}: {
  text: string
  onTextChange: (text: string) => void
  attachments: Attachment[]
  onAddFiles: (files: File[]) => void
  onRemoveAttachment: (localId: string) => void
  onOpenAttachment: (attachment: Attachment) => void
  onSend: () => void
  onStop: () => void
  streaming: boolean
  autoFocus?: boolean
}) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  // 내용에 맞춰 입력창 높이를 늘린다(최대 높이는 CSS에서 제한).
  useEffect(() => {
    const el = textarea.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [text])

  const uploading = attachments.some((a) => a.state === 'uploading')
  const ready = attachments.some((a) => a.state === 'ready')
  const canSend = !streaming && !uploading && (text.trim() !== '' || ready)

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 한글 조합 중 Enter는 글자 확정이므로 전송하지 않는다.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (canSend) onSend()
    }
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(e.clipboardData.files)
    if (files.length > 0) {
      e.preventDefault()
      onAddFiles(files)
    }
  }

  return (
    <div className="composer">
      {attachments.length > 0 && (
        <div className="composer-files">
          {attachments.map((a) => (
            <FileCard
              key={a.localId}
              attachment={a}
              onOpen={() => onOpenAttachment(a)}
              onRemove={() => onRemoveAttachment(a.localId)}
            />
          ))}
        </div>
      )}
      <textarea
        ref={textarea}
        value={text}
        onChange={(e) => onTextChange(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        placeholder="법원 문서를 첨부하거나 요청을 입력하세요"
        rows={1}
        autoFocus={autoFocus}
        aria-label="메시지"
      />
      <div className="composer-bar">
        <button
          type="button"
          className="icon-button"
          onClick={() => fileInput.current?.click()}
          aria-label="파일 첨부"
          title="파일 첨부"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path
              d="M16.5 6.5 8.4 14.6a2 2 0 0 0 2.8 2.8l8.5-8.5a4 4 0 0 0-5.7-5.7l-8.8 8.8a6 6 0 0 0 8.5 8.5l7.4-7.4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf"
          multiple
          hidden
          onChange={(e) => {
            onAddFiles(Array.from(e.target.files ?? []))
            e.target.value = ''
          }}
        />
        <span className="composer-hint">Enter 전송 · Shift+Enter 줄바꿈</span>
        {streaming ? (
          <button type="button" className="send-button stop" onClick={onStop} aria-label="응답 중지" title="중지">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
            </svg>
          </button>
        ) : (
          <button
            type="button"
            className="send-button"
            onClick={onSend}
            disabled={!canSend}
            aria-label="보내기"
            title={uploading ? '파일을 올리는 중입니다' : '보내기'}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M12 19V5m0 0-6 6m6-6 6 6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>
    </div>
  )
}
