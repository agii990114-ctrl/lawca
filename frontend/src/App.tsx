import { useState, type DragEvent } from 'react'
import { pdfUrl, uploadDocument, type DocumentResult } from './api'
import DeadlinePanel from './DeadlinePanel'
import ExtractionPanel from './ExtractionPanel'

export default function App() {
  const [result, setResult] = useState<DocumentResult | null>(null)
  const [page, setPage] = useState(1)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [done, setDone] = useState<Set<number>>(new Set())

  async function upload(file: File | undefined) {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const res = await uploadDocument(file)
      setResult(res)
      setPage(1)
      setDone(new Set())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    upload(e.dataTransfer.files[0])
  }

  function toggle(i: number) {
    const next = new Set(done)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    setDone(next)
  }

  return (
    <div className="app">
      <header>
        <h1>lawca</h1>
        <span className="muted">법원 문서 → 할 일 · 기한</span>
      </header>

      <p className="banner">
        개발 단계입니다. 올린 문서는 Gemini API로 전송되므로 실제 의뢰인 문서를 올리지 마세요.
      </p>

      <label
        className={`dropzone${dragging ? ' dragging' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          type="file"
          accept="application/pdf"
          disabled={uploading}
          onChange={(e) => upload(e.target.files?.[0])}
        />
        {uploading ? '문서를 읽는 중입니다. 20초 정도 걸릴 수 있습니다…' : '법원 문서 PDF를 끌어다 놓거나 눌러서 선택하세요'}
      </label>

      {error && <p className="issue error">{error}</p>}

      {result && (
        <div className="workspace">
          <section className="viewer">
            <div className="viewer-bar">
              <strong>{result.filename}</strong>
              <span className="muted">{page}쪽</span>
            </div>
            <iframe key={page} title="원문 PDF" src={pdfUrl(result.id, page)} />
          </section>

          <div className="side">
            <ExtractionPanel result={result} onShowPage={setPage} />
            <DeadlinePanel key={result.id} suggestions={result.suggestions} />
            <section className="panel">
              <h2>할 일</h2>
              <ul className="checklist">
                {result.checklist.map((item, i) => (
                  <li key={item}>
                    <label>
                      <input type="checkbox" checked={done.has(i)} onChange={() => toggle(i)} />
                      <span className={done.has(i) ? 'done' : undefined}>{item}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <p className="muted">체크 상태는 아직 저장되지 않습니다.</p>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
