import type { Attachment } from './types'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

function detail(a: Attachment): string {
  if (a.state === 'uploading') return `올리는 중 ${Math.round(a.progress * 100)}%`
  if (a.state === 'error') return a.error ?? '업로드 실패'
  const pages = a.pages ? ` · ${a.pages}쪽` : ''
  return `PDF · ${formatSize(a.size)}${pages}`
}

export default function FileCard({
  attachment,
  onOpen,
  onRemove,
}: {
  attachment: Attachment
  onOpen?: () => void
  onRemove?: () => void
}) {
  const openable = attachment.state === 'ready' && onOpen
  return (
    <div className={`file-card ${attachment.state}`}>
      <button
        type="button"
        className="file-card-body"
        onClick={openable ? onOpen : undefined}
        disabled={!openable}
        title={attachment.name}
      >
        <span className="file-icon" aria-hidden="true">
          PDF
        </span>
        <span className="file-text">
          <span className="file-name">{attachment.name}</span>
          <span className="file-detail">{detail(attachment)}</span>
        </span>
      </button>
      {attachment.state === 'uploading' && (
        <span className="file-progress" style={{ width: `${attachment.progress * 100}%` }} />
      )}
      {onRemove && (
        <button type="button" className="file-remove" onClick={onRemove} aria-label={`${attachment.name} 빼기`}>
          ×
        </button>
      )}
    </div>
  )
}
