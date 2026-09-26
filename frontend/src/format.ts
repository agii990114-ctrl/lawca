const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

// '2026-09-15' → '2026. 9. 15.(화)'
export function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const weekday = WEEKDAYS[new Date(y, m - 1, d).getDay()]
  return `${y}. ${m}. ${d}.(${weekday})`
}

export function todayIso(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

// ISO 시각 → '오후 3:05'
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' })
}
