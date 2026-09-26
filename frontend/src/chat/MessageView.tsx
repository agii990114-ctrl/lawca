import Markdown from 'react-markdown'
import DocumentCard from './DocumentCard'
import FileCard from './FileCard'
import type { Attachment, Message, Step } from './types'

function StepLine({ step }: { step: Step }) {
  const mark = step.state === 'done' ? '✓' : step.state === 'error' ? '!' : ''
  return (
    <li className={`step ${step.state}`}>
      <span className="step-mark" aria-hidden="true">
        {step.state === 'running' ? <span className="spinner" /> : mark}
      </span>
      {step.label}
    </li>
  )
}

export default function MessageView({
  message,
  onOpenFile,
}: {
  message: Message
  onOpenFile: (fileId: string, name: string, page?: number) => void
}) {
  if (message.role === 'user') {
    const open = (a: Attachment) => a.id && onOpenFile(a.id, a.name)
    return (
      <div className="message user">
        {message.attachments.length > 0 && (
          <div className="message-files">
            {message.attachments.map((a) => (
              <FileCard key={a.localId} attachment={a} onOpen={() => open(a)} />
            ))}
          </div>
        )}
        {message.text && <div className="bubble">{message.text}</div>}
      </div>
    )
  }

  const thinking = message.state === 'streaming' && !message.text && message.steps.length === 0
  return (
    <div className="message assistant">
      <div className="avatar" aria-hidden="true">
        L
      </div>
      <div className="assistant-body">
        {message.steps.length > 0 && (
          <ul className="steps">
            {message.steps.map((s) => (
              <StepLine key={s.id} step={s} />
            ))}
          </ul>
        )}
        {thinking && <span className="typing" aria-label="응답을 준비하는 중" />}
        {message.text && (
          <div className="markdown">
            <Markdown>{message.text}</Markdown>
          </div>
        )}
        {message.cards.map((card) => (
          <DocumentCard
            key={card.file_id}
            result={card}
            onShowPage={(page) => onOpenFile(card.file_id, card.filename, page)}
          />
        ))}
        {message.state === 'stopped' && <p className="muted">응답을 중지했습니다.</p>}
        {message.state === 'error' && <p className="issue error">응답 중 오류가 났습니다. 다시 시도해 주세요.</p>}
      </div>
    </div>
  )
}
