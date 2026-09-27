// 캘린더용 날짜 계산. 날짜는 'YYYY-MM-DD' 문자열(한국 시간 기준 달력 날짜)로 다룬다.

const pad = (n: number) => String(n).padStart(2, '0')

export function toIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function parseIso(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function addDays(iso: string, days: number): string {
  const d = parseIso(iso)
  d.setDate(d.getDate() + days)
  return toIso(d)
}

export function addMonths(iso: string, months: number): string {
  const d = parseIso(iso)
  return toIso(new Date(d.getFullYear(), d.getMonth() + months, 1))
}

// 그 주의 일요일
export function startOfWeek(iso: string): string {
  return addDays(iso, -parseIso(iso).getDay())
}

export type Mode = 'month' | 'week'

// 화면에 그릴 날짜들. 월 보기는 6주(42일), 주 보기는 7일.
export function visibleDays(anchor: string, mode: Mode): string[] {
  const d = parseIso(anchor)
  const first = mode === 'month' ? startOfWeek(toIso(new Date(d.getFullYear(), d.getMonth(), 1))) : startOfWeek(anchor)
  const count = mode === 'month' ? 42 : 7
  return Array.from({ length: count }, (_, i) => addDays(first, i))
}

export function title(anchor: string, mode: Mode): string {
  const d = parseIso(anchor)
  if (mode === 'month') return `${d.getFullYear()}년 ${d.getMonth() + 1}월`
  const days = visibleDays(anchor, 'week')
  const a = parseIso(days[0])
  const b = parseIso(days[6])
  const end = a.getMonth() === b.getMonth() ? `${b.getDate()}일` : `${b.getMonth() + 1}월 ${b.getDate()}일`
  return `${a.getFullYear()}년 ${a.getMonth() + 1}월 ${a.getDate()}일 – ${end}`
}

export const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

// '2026-10-15' → '10월 15일 (목)'
export function dayLabel(iso: string): string {
  const d = parseIso(iso)
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAYS[d.getDay()]})`
}
