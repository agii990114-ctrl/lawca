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

// 오늘부터 만료일까지 남은 날. 0이면 오늘 만료.
export function daysUntil(iso: string, today = todayIso()): number {
  const toUtc = (v: string) => {
    const [y, m, d] = v.split('-').map(Number)
    return Date.UTC(y, m - 1, d)
  }
  return Math.round((toUtc(iso) - toUtc(today)) / 86_400_000)
}

export function dDay(days: number): string {
  if (days === 0) return 'D-day'
  return days > 0 ? `D-${days}` : `D+${-days}`
}
