// Lado de marca das telas de entrar/criar conta: o mês da família num calendário, com as contas
// nos dias delas — mostra o que o FinFam faz em vez de um slogan. Decorativo (aria-hidden).
import { MONTHS } from '../lib/format'

type Bill = { day: number; label: string; state: 'paid' | 'due' | 'next' }
const BILLS: Bill[] = [
  { day: 5, label: 'Luz', state: 'paid' }, { day: 10, label: 'Água', state: 'paid' },
  { day: 14, label: 'Fatura', state: 'due' }, { day: 20, label: 'Escola', state: 'next' },
  { day: 27, label: 'Gás', state: 'next' },
]

function MonthGrid() {
  const now = new Date()
  const year = now.getFullYear(), month = now.getMonth()
  const lead = new Date(year, month, 1).getDay()            // domingo = 0
  const days = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)]
  const byDay = new Map(BILLS.map((b) => [b.day, b]))
  return (
    <figure className="aside-cal" aria-hidden="true">
      <figcaption>{MONTHS[month]} {year}</figcaption>
      <div className="aside-week">{['D', 'S', 'T', 'Q', 'Q', 'S', 'S'].map((d, i) => <span key={i}>{d}</span>)}</div>
      <div className="aside-days">
        {cells.map((d, i) => {
          const bill = d ? byDay.get(d) : undefined
          return (
            <span key={i} className={`aside-day ${d === now.getDate() ? 'today' : ''} ${bill ? `has-${bill.state}` : ''}`}>
              {d && <b>{d}</b>}
              {bill && <em>{bill.label}</em>}
            </span>
          )
        })}
      </div>
      <div className="aside-legend"><span className="k-paid">pago</span><span className="k-due">vence logo</span><span className="k-next">a pagar</span></div>
    </figure>
  )
}

export function AuthAside() {
  return (
    <aside className="auth-side">
      <div className="brand brand-lg"><img src="/logo.svg" alt="" width={40} height={40} /><span>FinFam</span></div>
      <div className="auth-side-body">
        <p className="auth-pitch">O mês da família, conta por conta.</p>
        <p className="auth-sub">Gastos, cartões, contas a pagar e metas de quem mora junto, num lugar só.</p>
        <MonthGrid />
      </div>
    </aside>
  )
}
