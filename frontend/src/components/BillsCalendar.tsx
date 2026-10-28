// Contas a pagar no calendário: cada conta no dia em que vence; compras no crédito somadas na fatura.
import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { brl, MONTHS } from '../lib/format'
import { useApi } from '../lib/hooks'
import { navigate } from '../lib/router'
import { useSession } from '../lib/session'
import type { Expense } from '../lib/types'
import { Badge, ErrorBox, Money, MonthNav, Panel, Spinner } from './ui'

type Status = 'pago' | 'pendente' | 'vencida'
interface Item {
  type: 'expense' | 'invoice'; id?: number; card_id?: number; label: string; detail: string | null
  amount: number; status: Status; count?: number; user_name?: string | null
}
interface CalendarData {
  year: number; month: number; today: string; days: Record<string, Item[]>
  totals: { total: number; paid: number; pending: number; overdue: number }
}

const WEEK = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
const STATUS_LABEL: Record<Status, string> = { pago: 'Pago', pendente: 'A pagar', vencida: 'Vencida' }
const STATUS_TONE: Record<Status, 'success' | 'info' | 'danger'> = { pago: 'success', pendente: 'info', vencida: 'danger' }
const iso = (y: number, m: number, d: number) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`

/** A situação que manda no dia: vencida > a pagar > pago. */
function dayTone(items: Item[]): Status | null {
  if (!items.length) return null
  if (items.some((i) => i.status === 'vencida')) return 'vencida'
  return items.some((i) => i.status === 'pendente') ? 'pendente' : 'pago'
}

function DayList({ date, items, month, year }: { date: string; items: Item[]; month: number; year: number }) {
  const { openExpense } = useSession()
  const open = async (i: Item) => {
    if (i.type === 'invoice') navigate(`/cards/${i.card_id}/invoice?month=${month}&year=${year}`)
    else if (i.id) openExpense(await api.get<Expense>(`/expenses/${i.id}`))
  }
  const [y, m, d] = date.split('-').map(Number)
  const total = items.reduce((s, i) => s + i.amount, 0)
  return (
    <Panel title={<>Dia {d} de {MONTHS[m - 1].toLowerCase()} <small className="muted">{y}</small></>}
      actions={items.length > 0 && <b><Money value={total} /></b>} flush>
      {items.length === 0 ? <p className="muted cal-empty">Nada vence neste dia.</p> : (
        <ul className="cal-list">
          {items.map((i, k) => (
            <li key={k}>
              <button type="button" className="cal-item" onClick={() => open(i)}>
                <span className="cal-item-main">
                  <b>{i.label}</b>
                  <small className="muted">
                    {i.type === 'invoice' ? `${i.detail} · ${i.count} ${i.count === 1 ? 'compra' : 'compras'}` : i.detail}
                    {i.user_name ? ` · ${i.user_name}` : ''}
                  </small>
                </span>
                <Money value={i.amount} className="cal-item-amount" />
                <Badge tone={STATUS_TONE[i.status]}>{STATUS_LABEL[i.status]}</Badge>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

export function BillsCalendar({ month, year, onPeriod }: { month: number; year: number; onPeriod: (m: number, y: number) => void }) {
  const { dataVersion } = useSession()
  const data = useApi(() => api.get<CalendarData>('/expenses/calendar', { month, year }), [month, year, dataVersion])
  const [picked, setPicked] = useState<string | null>(null)
  const d = data.data

  const cells = useMemo(() => {
    const lead = new Date(year, month - 1, 1).getDay()
    const total = new Date(year, month, 0).getDate()
    return [...Array<number | null>(lead).fill(null), ...Array.from({ length: total }, (_, i) => i + 1)]
  }, [month, year])

  if (data.error) return <ErrorBox message={data.error} onRetry={data.reload} />
  if (!d) return <Spinner />
  const inMonth = d.today.startsWith(iso(year, month, 1).slice(0, 7))
  const firstWithBills = Object.keys(d.days).sort()[0]
  const selected = picked && picked.startsWith(iso(year, month, 1).slice(0, 7)) ? picked
    : inMonth ? d.today : firstWithBills ?? iso(year, month, 1)

  return (
    <div className="stack-lg">
      <div className="cal-head">
        <MonthNav month={month} year={year} onChange={(m, y) => { setPicked(null); onPeriod(m, y) }} />
        <dl className="cal-totals">
          <div><dt>No mês</dt><dd><Money value={d.totals.total} /></dd></div>
          <div><dt>Pago</dt><dd className="t-paid"><Money value={d.totals.paid} /></dd></div>
          <div><dt>A pagar</dt><dd><Money value={d.totals.pending} /></dd></div>
          {d.totals.overdue > 0 && <div><dt>Vencido</dt><dd className="t-overdue"><Money value={d.totals.overdue} /></dd></div>}
        </dl>
      </div>
      <div className="cal" role="grid" aria-label={`Contas de ${MONTHS[month - 1]} de ${year}`}>
        <div className="cal-week" role="row">{WEEK.map((w) => <span key={w} role="columnheader">{w}</span>)}</div>
        <div className="cal-days">
          {cells.map((day, k) => {
            if (!day) return <span key={k} className="cal-day cal-blank" aria-hidden="true" />
            const date = iso(year, month, day)
            const items = d.days[date] ?? []
            const tone = dayTone(items)
            const sum = items.reduce((s, i) => s + i.amount, 0)
            return (
              <button key={k} type="button" role="gridcell" aria-selected={date === selected}
                className={`cal-day ${tone ? `tone-${tone}` : ''} ${date === d.today ? 'is-today' : ''} ${date === selected ? 'is-picked' : ''}`}
                aria-label={`${day}: ${items.length ? `${items.length} conta(s), ${brl(sum)}` : 'nada vence'}`}
                onClick={() => setPicked(date)}>
                <span className="cal-num">{day}</span>
                {items.slice(0, 3).map((i, n) => <span key={n} className={`cal-chip s-${i.status}`}>{i.label}</span>)}
                {items.length > 3 && <span className="cal-more">+{items.length - 3}</span>}
                {items.length > 0 && <span className="cal-dot" aria-hidden="true" />}
              </button>
            )
          })}
        </div>
        <div className="cal-legend"><span className="l-paid">Pago</span><span className="l-pending">A pagar</span><span className="l-overdue">Vencida</span></div>
      </div>
      <DayList date={selected} items={d.days[selected] ?? []} month={month} year={year} />
    </div>
  )
}
