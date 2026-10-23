import { useState } from 'react'
import { BarChart, Donut } from '../components/charts'
import { ExpenseList } from '../components/ExpenseList'
import { Badge, Button, Check, ConfirmButton, Empty, ErrorBox, Field, IconButton, Input, Modal, Money, MonthNav,
  PageHeader, Panel, Progress, Segmented, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl, parseMoney } from '../lib/format'
import { useApi, usePeriod } from '../lib/hooks'
import { Link, navigate } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Card, CardUsage, Expense } from '../lib/types'

type CardWithUsage = Card & { usage: CardUsage }
interface CardsData { month_name: string; cards: CardWithUsage[]; history: { labels: string[]; series: { label: string; data: number[]; color: string }[] } | null }

type Kind = 'credit' | 'debit' | 'both' | 'vr' | 'va'

function kindOf(c: Card): Kind {
  if (c.card_type === 'vr' || c.card_type === 'va') return c.card_type
  if (c.supports_credit && c.supports_debit) return 'both'
  return c.supports_debit ? 'debit' : 'credit'
}

const STATUS_LABEL: Record<string, string> = { aberta: 'Fatura aberta', fechada: 'Fatura fechada', futura: 'Fatura futura' }

function CardForm({ open, editing, onClose, onSaved }: { open: boolean; editing: Card | null; onClose: () => void; onSaved: () => void }) {
  const { me, meta, toast } = useSession()
  const blank = { user_id: String(me?.user.id ?? ''), kind: 'credit' as Kind, nickname: '', last_digits: '', bank: '',
    due_day: '10', best_buy_day: '3', credit_limit: '', monthly_amount: '', renewal_day: '1', is_virtual: false, is_additional: false }
  const [f, setF] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastOpen, setLastOpen] = useState(false)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) {
      setError(null)
      setF(editing ? { user_id: String(editing.user_id), kind: kindOf(editing), nickname: editing.nickname ?? '',
        last_digits: editing.last_digits, bank: editing.bank ?? '', due_day: String(editing.due_day),
        best_buy_day: String(editing.best_buy_day), credit_limit: editing.credit_limit ? String(editing.credit_limit).replace('.', ',') : '',
        monthly_amount: editing.monthly_amount ? String(editing.monthly_amount).replace('.', ',') : '',
        renewal_day: String(editing.renewal_day ?? 1), is_virtual: editing.is_virtual, is_additional: editing.is_additional } : blank)
    }
  }
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }))
  const isVr = f.kind === 'vr' || f.kind === 'va'
  const hasCredit = f.kind === 'credit' || f.kind === 'both'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const money = (s: string) => { const n = parseMoney(s); return Number.isFinite(n) ? n.toFixed(2) : null }
    const body = {
      user_id: Number(f.user_id), special_type: isVr ? f.kind : null,
      supports_credit: hasCredit, supports_debit: f.kind === 'debit' || f.kind === 'both',
      nickname: f.nickname || null, last_digits: f.last_digits, bank: f.bank || null,
      is_virtual: f.is_virtual, is_additional: f.is_additional,
      due_day: Number(f.due_day) || 1, best_buy_day: Number(f.best_buy_day) || 1,
      credit_limit: hasCredit ? money(f.credit_limit) : null,
      monthly_amount: isVr ? money(f.monthly_amount) : null, renewal_day: isVr ? Number(f.renewal_day) || null : null,
    }
    setBusy(true)
    try {
      const r = editing ? await api.put<{ message: string }>(`/cards/${editing.id}`, body) : await api.post<{ message: string }>('/cards', body)
      toast(r.message)
      onSaved()
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={editing ? 'Editar cartão' : 'Novo cartão'}
      footer={<><Button variant="ghost" onClick={onClose}>Cancelar</Button><Button type="submit" form="card-form" busy={busy}>Salvar cartão</Button></>}>
      <form id="card-form" className="form-grid" onSubmit={submit}>
        <div className="span-2">
          <Segmented ariaLabel="Tipo de cartão" value={f.kind} onChange={(v) => set('kind', v)} options={[
            { value: 'credit', label: 'Crédito' }, { value: 'debit', label: 'Débito' }, { value: 'both', label: 'Crédito e débito' },
            { value: 'vr', label: 'VR' }, { value: 'va', label: 'VA' }]} />
        </div>
        <Field label="Banco / bandeira"><Select value={f.bank} onChange={(e) => set('bank', e.target.value)} placeholder="Escolha" options={meta?.banks ?? []} /></Field>
        <Field label="Últimos 4 dígitos"><Input value={f.last_digits} onChange={(e) => set('last_digits', e.target.value.replace(/\D/g, '').slice(0, 4))} required pattern="\d{4}" inputMode="numeric" /></Field>
        <Field label="Apelido (opcional)"><Input value={f.nickname} onChange={(e) => set('nickname', e.target.value)} maxLength={50} /></Field>
        <Field label="Titular">
          <Select value={f.user_id} onChange={(e) => set('user_id', e.target.value)} disabled={Boolean(editing)}
            options={(me?.members ?? []).map((u) => ({ value: String(u.id), label: u.name }))} />
        </Field>
        {hasCredit && <>
          <Field label="Melhor dia de compra" hint="Compras depois deste dia vão para a próxima fatura"><Input type="number" min={1} max={31} value={f.best_buy_day} onChange={(e) => set('best_buy_day', e.target.value)} /></Field>
          <Field label="Dia de vencimento"><Input type="number" min={1} max={31} value={f.due_day} onChange={(e) => set('due_day', e.target.value)} /></Field>
          <Field label="Limite (R$)" hint="Cartões do mesmo banco compartilham o limite"><Input inputMode="decimal" value={f.credit_limit} onChange={(e) => set('credit_limit', e.target.value)} /></Field>
          <div className="checks"><Check label="Virtual" checked={f.is_virtual} onChange={(v) => set('is_virtual', v)} /><Check label="Adicional" checked={f.is_additional} onChange={(v) => set('is_additional', v)} /></div>
        </>}
        {isVr && <>
          <Field label="Crédito mensal (R$)"><Input inputMode="decimal" value={f.monthly_amount} onChange={(e) => set('monthly_amount', e.target.value)} /></Field>
          <Field label="Dia da recarga"><Input type="number" min={1} max={31} value={f.renewal_day} onChange={(e) => set('renewal_day', e.target.value)} /></Field>
        </>}
        {error && <p className="span-2 form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

function PlasticCard({ c, month, year, onEdit, onDelete }: { c: CardWithUsage; month: number; year: number; onEdit: () => void; onDelete: () => void }) {
  const u = c.usage
  return (
    <article className="card-tile">
      <Link to={`/cards/${c.id}/invoice?month=${month}&year=${year}`} className="plastic" style={{ background: u.gradient, color: u.text_color }}>
        <span className="plastic-bank">{c.nickname || c.bank || 'Cartão'}</span>
        <span className="plastic-type">{c.type_label}{c.is_virtual ? ' · Virtual' : c.is_additional ? ' · Adicional' : ''}</span>
        <span className="plastic-digits">•••• {c.last_digits}</span>
        <span className="plastic-owner">{c.owner_name}</span>
      </Link>
      <div className="card-tile-body">
        {u.vr_balance ? (
          <>
            <div className="row-between"><span className="muted">Saldo disponível</span><Money value={u.vr_balance.remaining} tone="income" /></div>
            <Progress value={u.vr_balance.pct_used} tone={u.vr_balance.pct_used > 85 ? 'danger' : 'primary'} />
            <small className="muted">Gasto {brl(u.vr_balance.spent)} de {brl(u.vr_balance.available)}{u.vr_balance.carry_over > 0 ? ` (inclui ${brl(u.vr_balance.carry_over)} do mês anterior)` : ''}</small>
          </>
        ) : (
          <>
            <div className="row-between">
              <span className="muted">{c.supports_credit ? 'Fatura' : 'Gasto no débito'}</span>
              <Money value={c.supports_credit ? u.credit_total : u.debit_total} />
            </div>
            {u.limit && u.pct !== null && (
              <>
                <Progress value={u.pct} tone={u.pct >= 90 ? 'danger' : u.pct >= 75 ? 'warning' : 'primary'} />
                <small className="muted">{Math.round(u.pct)}% do limite{u.account_cards > 1 ? ' compartilhado' : ''} · disponível {brl(u.available)}</small>
              </>
            )}
            {c.supports_credit && c.supports_debit && <small className="muted">Débito no mês: {brl(u.debit_total)}</small>}
          </>
        )}
        <div className="row-between card-tile-foot">
          {c.supports_credit && u.payment_status && u.payment_status.status !== 'sem_despesas'
            ? <Badge tone={u.payment_status.status === 'pago' ? 'success' : u.payment_status.status === 'parcial' ? 'warning' : 'neutral'}>
                {u.payment_status.status === 'pago' ? 'Fatura paga' : `${STATUS_LABEL[u.status] ?? ''}`}
              </Badge>
            : <span />}
          <span>
            <IconButton icon="pencil" label="Editar cartão" onClick={onEdit} />
            <ConfirmButton label="Excluir cartão" confirmLabel="Excluir" onConfirm={onDelete} />
          </span>
        </div>
      </div>
    </article>
  )
}

export function CardsPage() {
  const { month, year, setPeriod } = usePeriod()
  const { dataVersion } = useSession()
  const act = useAction()
  const data = useApi(() => api.get<CardsData>('/cards', { month, year }), [month, year, dataVersion])
  const [form, setForm] = useState<{ open: boolean; editing: Card | null }>({ open: false, editing: null })
  const d = data.data

  return (
    <div className="page">
      <PageHeader title="Cartões" subtitle={<MonthNav month={month} year={year} onChange={setPeriod} />}
        actions={<Button icon="plus" onClick={() => setForm({ open: true, editing: null })}>Novo cartão</Button>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : d.cards.length === 0 ? (
        <Empty icon="credit-card" title="Nenhum cartão cadastrado.">
          <p>Cadastre seus cartões para acompanhar faturas, limite e saldo de VR/VA.</p>
          <Button icon="plus" onClick={() => setForm({ open: true, editing: null })}>Cadastrar cartão</Button>
        </Empty>
      ) : (
        <>
          <div className="card-grid">
            {d.cards.map((c) => (
              <PlasticCard key={c.id} c={c} month={month} year={year} onEdit={() => setForm({ open: true, editing: c })}
                onDelete={() => act(() => api.del<{ message: string }>(`/cards/${c.id}`), data.reload)} />
            ))}
          </div>
          {d.history && (
            <Panel title="Últimos 6 meses por cartão">
              <BarChart ariaLabel="Gastos por cartão nos últimos 6 meses" labels={d.history.labels} series={d.history.series} />
            </Panel>
          )}
        </>
      )}
      <CardForm open={form.open} editing={form.editing} onSaved={data.reload} onClose={() => setForm({ open: false, editing: null })} />
    </div>
  )
}

interface InvoiceData {
  card: Card; month: number; year: number; month_name: string; gradient: string; text_color: string; status: string
  credit: { expenses: Expense[]; total: number; paid_total: number; pending_total: number; future_installments: number
    by_category: { labels: string[]; data: number[]; colors: string[] } | null }
  debit: { expenses: Expense[]; total: number; by_category: { labels: string[]; data: number[]; colors: string[] } | null }
  by_card: { card: Card; expenses: Expense[]; total: number; pending: number }[]
}

export function InvoicePage({ id }: { id: string }) {
  const { month, year, setPeriod } = usePeriod()
  const { dataVersion } = useSession()
  const act = useAction()
  const inv = useApi(() => api.get<InvoiceData>(`/cards/${id}/invoice`, { month, year }), [id, month, year, dataVersion])
  const [view, setView] = useState<'all' | 'cards'>('all')
  const d = inv.data
  if (inv.error) return <ErrorBox message={inv.error} onRetry={() => navigate('/cards')} />
  if (!d) return <Spinner />
  const hasCredit = d.card.supports_credit || !d.card.supports_debit

  return (
    <div className="page">
      <PageHeader title={d.card.label} subtitle={<MonthNav month={month} year={year} onChange={setPeriod} />}
        actions={<Link to={`/cards?month=${month}&year=${year}`} className="btn btn-ghost">Voltar aos cartões</Link>} />
      {hasCredit && (
        <div className="invoice-head" style={{ background: d.gradient, color: d.text_color }}>
          <div><span>Fatura de {d.month_name}</span><strong>{brl(d.credit.total)}</strong><small>{STATUS_LABEL[d.status]} · vence dia {d.card.due_day}</small></div>
          <div><span>Pago</span><strong>{brl(d.credit.paid_total)}</strong></div>
          <div><span>Falta pagar</span><strong>{brl(d.credit.pending_total)}</strong></div>
          <div><span>Parcelas futuras</span><strong>{brl(d.credit.future_installments)}</strong></div>
          {d.credit.pending_total > 0 && (
            <Button variant="subtle" icon="check-all" onClick={() => act(
              () => api.post<{ message: string }>(`/cards/${d.card.id}/invoice/pay`, { month, year }), inv.reload)}>Pagar fatura</Button>
          )}
        </div>
      )}
      <div className="grid-invoice">
        {hasCredit && (
          <Panel title="Compras no crédito" flush actions={d.by_card.length > 1 && (
            <Segmented ariaLabel="Agrupar" value={view} onChange={setView} options={[{ value: 'all', label: 'Tudo' }, { value: 'cards', label: 'Por cartão' }]} />)}>
            {d.credit.expenses.length === 0 ? <Empty title="Nenhuma compra nesta fatura." />
              : view === 'cards' && d.by_card.length > 1
                ? d.by_card.map((g) => (
                  <div key={g.card.id} className="invoice-group">
                    <h3>{g.card.label} <Money value={g.total} /></h3>
                    <ExpenseList items={g.expenses} onChange={inv.reload} showPerson={false} />
                  </div>))
                : <ExpenseList items={d.credit.expenses} onChange={inv.reload} />}
          </Panel>
        )}
        {d.credit.by_category && (
          <Panel title="Por categoria">
            <Donut labels={d.credit.by_category.labels} data={d.credit.by_category.data} colors={d.credit.by_category.colors} centerLabel="Fatura" ariaLabel="Fatura por categoria" />
          </Panel>
        )}
        {d.card.supports_debit && (
          <Panel title={<>Débito no mês <Money value={d.debit.total} /></>} flush>
            {d.debit.expenses.length === 0 ? <Empty title="Nenhuma compra no débito." /> : <ExpenseList items={d.debit.expenses} onChange={inv.reload} />}
          </Panel>
        )}
      </div>
    </div>
  )
}
