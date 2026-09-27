import { useState } from 'react'
import { issueFeed } from '../api'

// 개인 구독 주소. 주소는 만들 때 한 번만 보여 주고, 다시 만들면 예전 주소는 끊긴다.
export default function FeedDialog({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const local = ['localhost', '127.0.0.1'].includes(window.location.hostname)

  async function issue() {
    setError(null)
    setCopied(false)
    try {
      const { path } = await issueFeed()
      setUrl(window.location.origin + path)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function copy() {
    if (!url) return
    await navigator.clipboard.writeText(url).catch(() => undefined)
    setCopied(true)
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="캘린더 구독">
        <h2>휴대폰·Outlook에서 보기</h2>
        <p className="muted">
          구독 주소를 캘린더 앱에 한 번 넣으면 lawca의 기한과 일정이 자동으로 따라옵니다(앱마다 몇 시간 간격으로 새로 고침).
          미확정 기일은 제목 앞에 “(미확정)”이 붙습니다.
        </p>
        <ul className="feed-steps">
          <li>Google 캘린더(웹): 다른 캘린더 + → URL로 추가 → 주소 붙여넣기</li>
          <li>Outlook: 캘린더 추가 → 인터넷에서 구독 → 주소 붙여넣기</li>
        </ul>
        {local && (
          <p className="issue warning">
            지금은 이 PC(localhost)에서 실행 중이라 Google 캘린더처럼 외부 서버가 가져가는 앱은 주소에 접속할 수 없습니다. 법인 서버에 올린 뒤에 쓰세요.
          </p>
        )}
        {url ? (
          <div className="feed-url">
            <input readOnly value={url} onFocus={(e) => e.target.select()} />
            <button type="button" className="secondary small" onClick={copy}>
              {copied ? '복사됨' : '복사'}
            </button>
          </div>
        ) : null}
        {url && <p className="issue warning">이 주소를 아는 사람은 누구나 일정을 볼 수 있습니다. 다른 사람과 공유하지 마세요.</p>}
        {error && <p className="issue error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            닫기
          </button>
          <button type="button" className="primary" onClick={issue}>
            {url ? '다시 만들기' : '구독 주소 만들기'}
          </button>
        </div>
        <p className="muted small-note">주소를 다시 만들면 예전 주소는 더 이상 쓸 수 없습니다.</p>
      </div>
    </div>
  )
}
