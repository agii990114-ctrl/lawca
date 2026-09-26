import { fileUrl } from '../api'
import type { Preview } from './types'

export default function PreviewPanel({ preview, onClose }: { preview: Preview; onClose: () => void }) {
  return (
    <aside className="preview" aria-label="파일 미리보기">
      <header>
        <strong title={preview.name}>{preview.name}</strong>
        <span className="muted">{preview.page}쪽</span>
        <button type="button" className="icon-button" onClick={onClose} aria-label="미리보기 닫기">
          ×
        </button>
      </header>
      <iframe key={`${preview.fileId}-${preview.page}`} title={preview.name} src={fileUrl(preview.fileId, preview.page)} />
    </aside>
  )
}
