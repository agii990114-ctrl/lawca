import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  deleteLibrary,
  fileUrl,
  LIBRARY_KINDS,
  listLibrary,
  reindexLibrary,
  searchLibrary,
  uploadFinal,
  uploadLibrary,
  type LibraryDoc,
  type LibraryKind,
  type LibrarySearch,
} from '../api'
import { toIso } from '../calendar/dates'
import { formatDate } from '../format'

const UPLOAD_KINDS = LIBRARY_KINDS.filter((k) => k.value === 'filing' || k.value === 'form' || k.value === 'other')

// 검색어가 들어간 부분을 굵게 보여 준다.
function Highlight({ text, query }: { text: string; query: string }) {
  const terms = query.split(/\s+/).filter((t) => t.length >= 2)
  if (terms.length === 0) return <>{text}</>
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g')
  return (
    <>
      {text.split(pattern).map((part, i) => (terms.includes(part) ? <mark key={i}>{part}</mark> : part))}
    </>
  )
}

function Upload({ onUploaded }: { onUploaded: (doc: LibraryDoc) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [form, setForm] = useState<{ kind: LibraryKind; title: string; case_number: string }>({ kind: 'filing', title: '', case_number: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      onUploaded(await uploadLibrary(file, form))
      setFile(null)
      setForm({ ...form, title: '', case_number: '' })
      if (input.current) input.current.value = ''
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card library-upload" onSubmit={submit}>
      <h3>자료 올리기</h3>
      <p className="muted">
        법인에서 쓰던 서면·서식(PDF, DOCX, TXT)을 올리면 글을 읽어 검색할 수 있게 합니다. 채팅에서 처리한 법원 문서와 lawca가 만든 초안은 자동으로 들어갑니다.
        스캔본 PDF와 HWP는 아직 읽지 못합니다.
      </p>
      <div className="user-form-grid">
        <label>
          파일
          <input ref={input} type="file" accept=".pdf,.docx,.hwpx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
        </label>
        <label>
          종류
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as LibraryKind })}>
            {UPLOAD_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          제목(선택)
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="비우면 파일 이름" />
        </label>
        <label>
          사건번호(선택)
          <input value={form.case_number} onChange={(e) => setForm({ ...form, case_number: e.target.value })} placeholder="예: 2026가단51234" />
        </label>
      </div>
      {error && <p className="issue error">{error}</p>}
      <button type="submit" className="primary" disabled={busy || !file}>
        {busy ? '읽고 색인하는 중…' : '올리기'}
      </button>
    </form>
  )
}

export default function LibraryView() {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<LibraryKind | ''>('')
  const [result, setResult] = useState<LibrarySearch | null>(null)
  const [docs, setDocs] = useState<LibraryDoc[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)

  const load = useCallback(() => {
    listLibrary(kind || undefined)
      .then(setDocs)
      .catch((e: Error) => setError(e.message))
  }, [kind])

  useEffect(load, [load])

  async function search(e?: FormEvent) {
    e?.preventDefault()
    if (!query.trim()) {
      setResult(null)
      return
    }
    setSearching(true)
    setError(null)
    try {
      setResult(await searchLibrary(query.trim(), kind || undefined))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSearching(false)
    }
  }

  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      load()
      if (result) search()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="library-view">
      <header className="view-header">
        <div>
          <h1>자료실</h1>
          <p className="muted">과거 서면·서식과 처리한 문서를 낱말과 뜻으로 함께 찾습니다. 검색은 이 PC의 로컬 모델로 하며 문서가 밖으로 나가지 않습니다.</p>
        </div>
      </header>

      <form className="library-search" onSubmit={search}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="예: 피고 주소를 모를 때 공시송달 신청 사유" aria-label="검색어" />
        <select value={kind} onChange={(e) => setKind(e.target.value as LibraryKind | '')} aria-label="종류">
          <option value="">전체</option>
          {LIBRARY_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        <button type="submit" className="primary" disabled={searching}>
          {searching ? '찾는 중…' : '검색'}
        </button>
      </form>

      {error && <p className="issue error">{error}</p>}

      {result && (
        <section className="card">
          <header className="card-header">
            <h3>
              검색 결과 {result.hits.length}건 · “{result.query}”
            </h3>
            {!result.semantic && <span className="status-tag tentative">키워드만(임베딩 모델 꺼짐)</span>}
          </header>
          {result.hits.length === 0 ? (
            <p className="muted">찾은 자료가 없습니다.</p>
          ) : (
            <ul className="search-list">
              {result.hits.map((h) => (
                <li key={h.doc.id}>
                  <div className="search-title">
                    <span className={`kind-tag ${h.doc.kind}`}>{h.doc.kind_label}</span>
                    <a href={fileUrl(h.doc.file_id, h.page ?? 1)} target="_blank" rel="noreferrer">
                      {h.doc.title}
                    </a>
                    {h.doc.status_label && <span className="status-tag">{h.doc.status_label}</span>}
                  </div>
                  <p className="search-snippet">
                    <Highlight text={h.snippet} query={result.query} />
                  </p>
                  <p className="muted small-note">
                    {[h.doc.case_number, h.page ? `${h.page}쪽` : null, formatDate(toIso(new Date(h.doc.created_at)))].filter(Boolean).join(' · ')}
                    {' · '}
                    {h.matched.includes('keyword') && h.matched.includes('semantic')
                      ? '낱말·뜻 모두 맞음'
                      : h.matched.includes('keyword')
                        ? '낱말이 맞음'
                        : '뜻이 비슷함'}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <Upload
        onUploaded={() => {
          load()
        }}
      />

      <section>
        <h3>자료 {docs ? `${docs.length}건` : ''}</h3>
        {docs && docs.length === 0 && <p className="muted">아직 자료가 없습니다.</p>}
        {docs && docs.length > 0 && (
          <div className="table-scroll">
            <table className="users-table">
              <thead>
                <tr>
                  <th>제목</th>
                  <th>종류</th>
                  <th>사건</th>
                  <th>올린 사람</th>
                  <th>올린 날</th>
                  <th>검색</th>
                  <th aria-label="작업" />
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td className="wrap">
                      <a href={fileUrl(d.file_id)} target="_blank" rel="noreferrer">
                        {d.title}
                      </a>
                    </td>
                    <td>
                      <span className={`kind-tag ${d.kind}`}>{d.kind_label}</span>
                      {d.status_label && <div className="muted small-note">{d.status_label}</div>}
                    </td>
                    <td>{d.case_number ?? '—'}</td>
                    <td>{d.created_by}</td>
                    <td>{formatDate(toIso(new Date(d.created_at)))}</td>
                    <td className="muted">{d.embedded ? `조각 ${d.chunk_count}` : '키워드만'}</td>
                    <td className="actions">
                      {d.draft_id && (
                        <label className="link-button file-link" title="워드에서 고쳐 실제로 낸 최종본(DOCX·PDF)을 올리면 자료실에 초안 대신 최종본이 들어갑니다">
                          {d.status_label === '최종본' ? '최종본 다시 올리기' : '최종본 올리기'}
                          <input
                            type="file"
                            accept=".docx,.pdf"
                            hidden
                            onChange={(e) => {
                              const file = e.target.files?.[0]
                              e.target.value = ''
                              if (file) run(() => uploadFinal(d.draft_id!, file))
                            }}
                          />
                        </label>
                      )}
                      {!d.embedded && (
                        <button type="button" className="link-button" onClick={() => run(() => reindexLibrary(d.id))}>
                          다시 색인
                        </button>
                      )}
                      {d.can_delete && (
                        <button
                          type="button"
                          className="link-button danger"
                          onClick={() => window.confirm(`'${d.title}'을(를) 자료실에서 뺄까요?`) && run(() => deleteLibrary(d.id))}
                        >
                          삭제
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
