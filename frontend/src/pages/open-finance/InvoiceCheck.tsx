import { useState } from 'react'
import { Badge, ErrorBox, MonthNav, Panel, Spinner } from '../../components/ui'
import { api } from '../../lib/api'
import { brl, dayMonth } from '../../lib/format'
import { useApi } from '../../lib/hooks'

interface CheckItem { id: number; description: string; amount: number; date: string; installment?: number | null }
interface CheckRow {
  account_id: number; label: string; linked: boolean; bank_total: number | null; bank_source: 'fatura' | 'lancamentos' | 'sem_fatura'
  finfam_total: number | null; diff: number | null; due_date: string | null
  bank_items_total: number; items_match: boolean
  only_bank: CheckItem[]; only_finfam: CheckItem[]
}

function CheckList({ title, items }: { title: string; items: CheckItem[] }) {
  if (!items.length) return null
  return (
    <div className="stack-sm">
      <b className="small">{title} ({items.length})</b>
      <ul className="check-list">
        {items.map((i) => (
          <li key={i.id}><span className="muted">{dayMonth(i.date)}</span> {i.description}
            {i.installment ? <span className="muted"> · parcela {i.installment}</span> : null}<b>{brl(i.amount)}</b></li>
        ))}
      </ul>
    </div>
  )
}

function Verdict({ c, ok, adjust }: { c: CheckRow; ok: boolean; adjust: boolean }) {
  if (!c.linked) return <Badge tone="warning">ligue a um cartão do FinFam</Badge>
  if (c.bank_source === 'sem_fatura') return <Badge tone="info">banco não informa a fatura</Badge>
  if (ok) return <Badge tone="success">bate</Badge>
  if (adjust) return <Badge tone="success">compras batem</Badge>
  return <Badge tone="danger">diferença {brl(c.diff)}</Badge>
}

function CheckCard({ c }: { c: CheckRow }) {
  const [open, setOpen] = useState(false)
  const ok = c.diff !== null && Math.abs(c.diff) < 0.01
  const adjust = !ok && c.diff !== null && c.items_match  // compras batem; o total do banco tem crédito/ajuste
  const diffs = c.only_bank.length + c.only_finfam.length
  return (
    <li>
      <div className="check-head">
        <b>{c.label}</b>
        <Verdict c={c} ok={ok} adjust={adjust} />
      </div>
      <div className="muted small">
        {c.bank_total === null ? 'O banco não diz em que fatura cada compra está'
          : <>Banco {brl(c.bank_total)} ({c.bank_source === 'fatura' ? 'fatura do banco' : 'lançamentos até agora'})</>}
        {c.finfam_total !== null && <> · FinFam {brl(c.finfam_total)}</>}
        {c.due_date && <> · vence {dayMonth(c.due_date)}</>}
      </div>
      {adjust && (
        <div className="small">
          As compras que o banco mandou para esta fatura somam {brl(c.bank_items_total)}, igual ao FinFam.
          O total que o banco cobra é {brl(Math.abs(c.diff!))} {c.diff! > 0 ? 'menor' : 'maior'}: é crédito ou ajuste
          da fatura (ex.: pagamento a mais no mês anterior), não compra faltando.
        </div>
      )}
      {diffs > 0 && (
        <div className="stack-sm">
          <div><button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Esconder diferenças' : `Ver diferenças (${diffs})`}</button></div>
          {open && <>
            <CheckList title="Só no banco (para revisar)" items={c.only_bank} />
            <CheckList title="Só no FinFam (lançado à mão ou parcela futura)" items={c.only_finfam} />
          </>}
        </div>
      )}
    </li>
  )
}

/** Fatura do banco × FinFam, por cartão conectado, no mês escolhido. */
export function InvoiceCheck() {
  const today = new Date()
  const [period, setPeriod] = useState({ month: today.getMonth() + 1, year: today.getFullYear() })
  const data = useApi(() => api.get<{ cards: CheckRow[] }>(`/pluggy/invoice-check?month=${period.month}&year=${period.year}`),
    [period.month, period.year])
  return (
    <Panel title="Conferir faturas com o banco" actions={
      <MonthNav month={period.month} year={period.year} onChange={(month, year) => setPeriod({ month, year })} />}>
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !data.data ? <Spinner /> : (
        <ul className="check-rows">{data.data.cards.map((c) => <CheckCard key={c.account_id} c={c} />)}</ul>
      )}
      <p className="muted small">"Fatura do banco" é o total que o banco informa; sem ele, soma os lançamentos que já chegaram.
        "Compras batem" quer dizer que cada compra do banco está no FinFam e só o total do banco tem um crédito ou ajuste à parte.</p>
    </Panel>
  )
}
