import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createEvent,
  getCalendar,
  getPending,
  updateEvent,
  type CalendarData,
  type CalendarItem,
  type EventInput,
  type PendingDocument,
} from '../api'
import { todayIso } from '../format'
import { addDays, addMonths, dayLabel, parseIso, title, visibleDays, WEEKDAYS, type Mode } from './dates'
import EventForm from './EventForm'
import FeedDialog from './FeedDialog'
import ItemDetail from './ItemDetail'
import PendingList from './PendingList'

const MAX_CHIPS = 3

function chipClass(item: CalendarItem) {
  return `chip ${item.source} ${item.status}`
}

function Chip({ item, onSelect }: { item: CalendarItem; onSelect: () => void }) {
  return (
    <button
      type="button"
      className={chipClass(item)}
      title={`${item.status === 'tentative' ? '(미확정) ' : ''}${item.time ? item.time + ' ' : ''}${item.title}`}
      onClick={(e) => {
        e.stopPropagation()
        onSelect()
      }}
    >
      {item.time && <span className="chip-time">{item.time}</span>}
      {item.title}
    </button>
  )
}

export default function CalendarView({ onOpenFile }: { onOpenFile: (fileId: string, name: string) => void }) {
  const today = todayIso()
  const [mode, setMode] = useState<Mode>('month')
  const [anchor, setAnchor] = useState(today)
  const [mine, setMine] = useState(false)
  const [data, setData] = useState<CalendarData | null>(null)
  const [pending, setPending] = useState<PendingDocument[]>([])
  const [error, setError] = useState<string | null>(null)
  const [selectedDay, setSelectedDay] = useState(today)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [form, setForm] = useState<{ day: string; editing?: CalendarItem } | null>(null)
  const [feedOpen, setFeedOpen] = useState(false)

  const days = useMemo(() => visibleDays(anchor, mode), [anchor, mode])
  const month = parseIso(anchor).getMonth()

  const load = useCallback(() => {
    getCalendar(days[0], days[days.length - 1], mine)
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
    getPending()
      .then(setPending)
      .catch(() => undefined)
  }, [days, mine])

  useEffect(load, [load])

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarItem[]>()
    for (const item of data?.items ?? []) map.set(item.day, [...(map.get(item.day) ?? []), item])
    return map
  }, [data])
  const holidays = useMemo(() => new Map((data?.holidays ?? []).map((h) => [h.day, h.name])), [data])
  const selected = data?.items.find((i) => i.id === selectedId) ?? null
  const dayItems = byDay.get(selectedDay) ?? []

  function move(step: number) {
    setAnchor(mode === 'month' ? addMonths(anchor, step) : addDays(anchor, step * 7))
  }

  function select(day: string, id: string | null = null) {
    setSelectedDay(day)
    setSelectedId(id)
  }

  async function save(input: EventInput) {
    if (form?.editing) {
      const { title, day, time, location, memo } = input
      await updateEvent(form.editing.id, { title, day, location: location ?? '', memo, ...(time ? { time } : { clear_time: true }) })
    } else {
      await createEvent(input)
    }
    setForm(null)
    select(input.day)
    load()
  }

  function dayClass(day: string) {
    const weekday = parseIso(day).getDay()
    return [
      'day',
      mode === 'month' && parseIso(day).getMonth() !== month ? 'outside' : '',
      day === today ? 'today' : '',
      day === selectedDay ? 'selected' : '',
      weekday === 0 || holidays.has(day) ? 'holiday' : weekday === 6 ? 'saturday' : '',
    ]
      .filter(Boolean)
      .join(' ')
  }

  return (
    <div className="calendar-view">
      <header className="calendar-header">
        <div className="calendar-nav">
          <button type="button" className="secondary small" onClick={() => move(-1)} aria-label="이전">
            ‹
          </button>
          <button
            type="button"
            className="secondary small"
            onClick={() => {
              setAnchor(today)
              select(today)
            }}
          >
            오늘
          </button>
          <button type="button" className="secondary small" onClick={() => move(1)} aria-label="다음">
            ›
          </button>
          <h1>{title(anchor, mode)}</h1>
        </div>
        <div className="calendar-tools">
          <div className="segmented" role="group" aria-label="보기">
            <button type="button" className={mode === 'month' ? 'active' : undefined} onClick={() => setMode('month')}>
              월
            </button>
            <button
              type="button"
              className={mode === 'week' ? 'active' : undefined}
              onClick={() => {
                setMode('week')
                setAnchor(selectedDay)
              }}
            >
              주
            </button>
          </div>
          <div className="segmented" role="group" aria-label="범위">
            <button type="button" className={!mine ? 'active' : undefined} onClick={() => setMine(false)}>
              전체
            </button>
            <button type="button" className={mine ? 'active' : undefined} onClick={() => setMine(true)}>
              내 일정
            </button>
          </div>
          <button type="button" className="primary small" onClick={() => setForm({ day: selectedDay })}>
            + 일정
          </button>
          <button type="button" className="secondary small" onClick={() => setFeedOpen(true)}>
            휴대폰·Outlook
          </button>
        </div>
      </header>

      {error && <p className="issue error">{error}</p>}

      <div className="calendar-body">
        <div className={`calendar-grid ${mode}`}>
          {WEEKDAYS.map((w, i) => (
            <div key={w} className={`weekday${i === 0 ? ' holiday' : i === 6 ? ' saturday' : ''}`}>
              {w}
            </div>
          ))}
          {days.map((day) => {
            const items = byDay.get(day) ?? []
            const limit = mode === 'month' ? MAX_CHIPS : items.length
            return (
              <div key={day} className={dayClass(day)} onClick={() => select(day)} role="button" tabIndex={0} aria-label={dayLabel(day)}
                onKeyDown={(e) => e.key === 'Enter' && select(day)}>
                <div className="day-head">
                  <span className="day-number">{parseIso(day).getDate()}</span>
                  {holidays.has(day) && <span className="holiday-name">{holidays.get(day)}</span>}
                </div>
                <div className="chips">
                  {items.slice(0, limit).map((item) => (
                    <Chip key={item.id} item={item} onSelect={() => select(day, item.id)} />
                  ))}
                  {items.length > limit && <span className="more">+{items.length - limit}</span>}
                </div>
                {items.length > 0 && (
                  <div className="dots" aria-hidden="true">
                    {items.slice(0, 4).map((item) => (
                      <span key={item.id} className={`dot ${item.source} ${item.status}`} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <aside className="calendar-side">
          <section className="day-panel">
            <h3>
              {dayLabel(selectedDay)}
              {holidays.has(selectedDay) && <span className="holiday-name"> {holidays.get(selectedDay)}</span>}
            </h3>
            {dayItems.length === 0 ? (
              <p className="muted">일정이 없습니다.</p>
            ) : (
              <ul className="day-list">
                {dayItems.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`${chipClass(item)} wide${item.id === selectedId ? ' active' : ''}`}
                      onClick={() => setSelectedId(item.id === selectedId ? null : item.id)}
                    >
                      {item.time && <span className="chip-time">{item.time}</span>}
                      {item.status === 'tentative' && <span className="chip-flag">미확정</span>}
                      {item.title}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {selected && selected.day === selectedDay && (
              <ItemDetail
                item={selected}
                onChanged={load}
                onEdit={(item) => setForm({ day: item.day, editing: item })}
                onOpenFile={onOpenFile}
              />
            )}
            <div className="legend">
              <span className="chip deadline confirmed">기한</span>
              <span className="chip hearing confirmed">기일</span>
              <span className="chip manual confirmed">일정</span>
              <span className="chip hearing tentative">미확정</span>
            </div>
          </section>

          {pending.length > 0 && <PendingList items={pending} onChanged={load} onOpenFile={onOpenFile} />}
        </aside>
      </div>

      {form && <EventForm initialDay={form.day} editing={form.editing} onSubmit={save} onClose={() => setForm(null)} />}
      {feedOpen && <FeedDialog onClose={() => setFeedOpen(false)} />}
    </div>
  )
}
