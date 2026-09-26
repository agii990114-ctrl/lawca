import { useId, useState } from 'react'
import { computeDeadline, type DeadlineResult, type Suggestion, type Unit } from './api'
import { formatDate, todayIso } from './format'

type ServiceKind = 'electronic_confirmed' | 'electronic_deemed' | 'paper'

const SERVICE_LABELS: Record<ServiceKind, string> = {
  electronic_confirmed: '전자소송에서 확인한 날',
  electronic_deemed: '전자소송 간주 송달일',
  paper: '종이 문서를 받은 날',
}

const MANUAL = -1

export default function DeadlinePanel({ suggestions }: { suggestions: Suggestion[] }) {
  const [choice, setChoice] = useState(suggestions.length > 0 ? 0 : MANUAL)
  const [manual, setManual] = useState<{ amount: number; unit: Unit }>({ amount: 7, unit: '일' })
  const [eventDate, setEventDate] = useState(todayIso())
  const [service, setService] = useState<ServiceKind>('electronic_confirmed')
  const [result, setResult] = useState<DeadlineResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const radioName = useId()
  const selected = choice === MANUAL ? null : suggestions[choice]

  async function calculate() {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const source =
        selected?.kind === 'statutory' && selected.rule_id
          ? { rule_id: selected.rule_id }
          : { period: selected?.period ?? { ...manual, label: '' } }
      setResult(
        await computeDeadline({
          event_date: eventDate,
          deemed_electronic_service: service === 'electronic_deemed',
          ...source,
        }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="card">
      <h3>기한 계산</h3>
      <fieldset>
        <legend>기간</legend>
        {suggestions.map((s, i) => (
          <label key={s.label} className="choice">
            <input type="radio" name={radioName} checked={choice === i} onChange={() => setChoice(i)} />
            <span>
              {s.label}
              {s.period && s.kind === 'statutory' ? ` · ${s.period.label}` : ''}
              {s.note && <span className="muted"> · {s.note}</span>}
            </span>
          </label>
        ))}
        <label className="choice">
          <input type="radio" name={radioName} checked={choice === MANUAL} onChange={() => setChoice(MANUAL)} />
          <span>직접 입력</span>
          {choice === MANUAL && (
            <span className="inline-inputs">
              <input
                type="number"
                min={1}
                value={manual.amount}
                onChange={(e) => setManual({ ...manual, amount: Number(e.target.value) })}
                aria-label="기간 숫자"
              />
              <select
                value={manual.unit}
                onChange={(e) => setManual({ ...manual, unit: e.target.value as Unit })}
                aria-label="기간 단위"
              >
                <option value="일">일</option>
                <option value="주">주</option>
                <option value="월">개월</option>
                <option value="년">년</option>
              </select>
            </span>
          )}
        </label>
      </fieldset>

      <fieldset>
        <legend>송달일</legend>
        <p className="muted">송달일은 문서에 적혀 있지 않습니다. 직접 입력하세요.</p>
        <div className="row">
          <input type="date" value={eventDate} onChange={(e) => setEventDate(e.target.value)} aria-label="송달일" />
          <select value={service} onChange={(e) => setService(e.target.value as ServiceKind)} aria-label="송달 유형">
            {Object.entries(SERVICE_LABELS).map(([kind, label]) => (
              <option key={kind} value={kind}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </fieldset>

      <button type="button" className="primary" onClick={calculate} disabled={loading || !eventDate}>
        {loading ? '계산 중…' : '기한 계산'}
      </button>

      {error && <p className="issue error">{error}</p>}

      {result && (
        <div className="result">
          <div className="deadline">
            <span className="muted">만료일</span>
            <strong>{formatDate(result.deadline)}</strong>
            <span className="muted">이날이 끝날 때 기간이 만료됩니다. 확정 전에 확인하세요.</span>
          </div>
          <dl>
            <dt>기간</dt>
            <dd>{result.period.label}</dd>
            <dt>기산일</dt>
            <dd>{formatDate(result.count_start)}</dd>
            <dt>연장 전 말일</dt>
            <dd>{formatDate(result.nominal_end)}</dd>
            {result.extended_over.length > 0 && (
              <>
                <dt>연장</dt>
                <dd>{result.extended_over.map((d) => `${formatDate(d.day)} ${d.reason}`).join(' → ')}</dd>
              </>
            )}
            <dt>근거</dt>
            <dd>{result.basis.join(', ')}</dd>
          </dl>
          {result.warnings.map((w) => (
            <p key={w} className="issue warning">
              {w}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}
