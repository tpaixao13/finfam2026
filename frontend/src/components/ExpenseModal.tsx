// Modal de lançamento/edição de despesa — disponível em todas as páginas.
import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { brl, dateBR, parseMoney, todayISO } from '../lib/format'
import { useSession } from '../lib/session'
import type { Card, Expense } from '../lib/types'
import { Button, Check, Field, Input, Modal, Segmented, Select } from './ui'

const CREDIT = 'Cartão de Crédito'
const CARD_METHODS = new Set([CREDIT, 'Cartão de Débito', 'VR', 'VA'])

interface Preview {
  invoice_label: string; is_open: boolean; days_to_closing: number | null; due_day: number
  credit_limit: number | null; projected_usage: number | null; is_shared_limit: boolean
}

interface FormState {
  user_id: string; description: string; amount: string; category: string; payment_method: string
  bank: string; card_id: string; date: string; paid: boolean; mode: 'avista' | 'parcelado' | 'recorrente'
  installments: string; recurring_months: string; apply_to_group: boolean
}

function initial(expense: Expense | null | undefined, preset: Partial<Expense> | undefined, userId: number): FormState {
  const e = expense ?? (preset as Expense | undefined)
  return {
    user_id: String(e?.user_id ?? userId),
    description: e?.description ?? '',
    amount: e?.amount ? String(e.amount).replace('.', ',') : '',
    category: e?.category ?? '',
    payment_method: e?.payment_method ?? 'PIX',
    bank: e?.bank ?? '',
    card_id: e?.card_id ? String(e.card_id) : '',
    date: e?.purchase_date ?? todayISO(),
    paid: e?.paid ?? false,
    mode: 'avista', installments: '2', recurring_months: '12', apply_to_group: false,
  }
}

function cardFits(card: Card, method: string) {
  if (method === CREDIT) return card.supports_credit
  if (method === 'Cartão de Débito') return card.supports_debit
  if (method === 'VR' || method === 'VA') return card.card_type === method.toLowerCase()
  return false
}

export function ExpenseModal() {
  const { expenseModal, closeExpense, me, meta, toast, bumpData } = useSession()
  const { open, expense, preset } = expenseModal
  const editing = Boolean(expense)
  const [f, setF] = useState<FormState>(() => initial(null, undefined, me?.user.id ?? 0))
  const [cards, setCards] = useState<Card[] | null>(null)  // null = ainda carregando
  const [categories, setCategories] = useState<string[]>([])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggested, setSuggested] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !me) return
    setF(initial(expense, preset, me.user.id))
    setError(null)
    setPreview(null)
    setSuggested(null)
    setCards(null)
    api.get<{ cards: (Card & { usage: unknown })[] }>('/cards').then((r) => setCards(r.cards)).catch(() => setCards([]))
    api.get<{ categories: string[] }>('/expenses/options').then((r) => setCategories(r.categories)).catch(() => undefined)
  }, [open, expense, preset, me])

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((s) => ({ ...s, [k]: v }))
  const usableCards = useMemo(() => (cards ?? []).filter((c) => cardFits(c, f.payment_method)), [cards, f.payment_method])
  const showBank = (meta?.bank_payment_methods ?? []).includes(f.payment_method) && !f.card_id
  const amount = parseMoney(f.amount)

  // Sugestão de categoria pela descrição (regras + histórico)
  useEffect(() => {
    if (!open || editing || f.description.trim().length < 3 || f.category) { setSuggested(null); return }
    const t = setTimeout(() => {
      api.get<{ category: string | null }>('/expenses/suggest-category', { description: f.description.trim() })
        .then((r) => setSuggested(r.category)).catch(() => undefined)
    }, 400)
    return () => clearTimeout(t)
  }, [f.description, f.category, open, editing])

  // Em qual fatura a compra cai
  useEffect(() => {
    if (!open || f.payment_method !== CREDIT || !f.card_id || !f.date) { setPreview(null); return }
    const [y, m, d] = f.date.split('-').map(Number)
    const t = setTimeout(() => {
      api.get<Preview>(`/cards/${f.card_id}/invoice-preview`,
        { day: d, month: m, year: y, amount: Number.isFinite(amount) ? amount : 0 })
        .then(setPreview).catch(() => setPreview(null))
    }, 250)
    return () => clearTimeout(t)
  }, [open, f.payment_method, f.card_id, f.date, amount])

  useEffect(() => {
    // só limpa depois que a lista chegou: antes disso o cartão da despesa ainda "não existe"
    if (cards && f.card_id && !usableCards.some((c) => String(c.id) === f.card_id)) set('card_id', '')
    if (f.payment_method !== CREDIT && f.mode === 'parcelado') set('mode', 'avista')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.payment_method, usableCards, cards])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!Number.isFinite(amount) || amount <= 0) { setError('Informe um valor maior que zero.'); return }
    const body: Record<string, unknown> = {
      user_id: Number(f.user_id), description: f.description.trim(), amount: amount.toFixed(2),
      category: f.category.trim(), payment_method: f.payment_method, bank: showBank ? f.bank || null : null,
      card_id: CARD_METHODS.has(f.payment_method) && f.card_id ? Number(f.card_id) : null,
      date: f.date, paid: f.paid,
    }
    setBusy(true)
    setError(null)
    try {
      let message: string
      if (editing && expense) {
        const r = await api.patch<{ message: string }>(`/expenses/${expense.id}`, { ...body, apply_to_group: f.apply_to_group })
        message = r.message
      } else {
        if (f.mode === 'parcelado') body.installments = Number(f.installments)
        if (f.mode === 'recorrente') body.recurring_months = Number(f.recurring_months)
        const r = await api.post<{ message: string; duplicate: boolean }>('/expenses', body)
        message = r.message
        if (r.duplicate) { toast(message, 'warning'); message = '' }
      }
      if (message) toast(message)
      bumpData()
      closeExpense()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const perInstallment = f.mode === 'parcelado' && Number.isFinite(amount) ? amount / Number(f.installments) : null

  return (
    <Modal open={open} onClose={closeExpense} title={editing ? 'Editar despesa' : 'Nova despesa'}
      footer={<>
        <Button variant="ghost" type="button" onClick={closeExpense}>Cancelar</Button>
        <Button type="submit" form="expense-form" busy={busy}>{editing ? 'Salvar alterações' : 'Lançar despesa'}</Button>
      </>}>
      <form id="expense-form" className="form-grid" onSubmit={submit}>
        <Field label="Descrição" className="span-2">
          <Input value={f.description} onChange={(e) => set('description', e.target.value)} required maxLength={200}
            placeholder="Ex.: Mercado, Conta de luz, Uber" autoFocus />
        </Field>
        <Field label={f.mode === 'parcelado' ? 'Valor total (R$)' : 'Valor (R$)'}
          hint={perInstallment ? `${f.installments}× de ${brl(perInstallment)}` : undefined}>
          <Input inputMode="decimal" value={f.amount} onChange={(e) => set('amount', e.target.value)} required placeholder="0,00" />
        </Field>
        <Field label="Data da compra">
          <Input type="date" value={f.date} onChange={(e) => set('date', e.target.value)} required />
        </Field>
        <Field label="Categoria" hint={suggested ? (
          <button type="button" className="link-btn" onClick={() => set('category', suggested)}>Usar “{suggested}”</button>) : undefined}>
          <Input list="category-list" value={f.category} onChange={(e) => set('category', e.target.value)} required maxLength={50} />
          <datalist id="category-list">{categories.map((c) => <option key={c} value={c} />)}</datalist>
        </Field>
        <Field label="Quem gastou">
          <Select value={f.user_id} onChange={(e) => set('user_id', e.target.value)}
            options={(me?.members ?? []).map((u) => ({ value: u.id, label: u.name }))} />
        </Field>
        <Field label="Forma de pagamento" className="span-2">
          <Select value={f.payment_method} onChange={(e) => set('payment_method', e.target.value)}
            options={meta?.payment_methods ?? ['PIX']} />
        </Field>
        {CARD_METHODS.has(f.payment_method) && (
          <Field label="Cartão" className="span-2"
            hint={usableCards.length === 0 ? 'Nenhum cartão cadastrado para esta forma de pagamento.' : undefined}>
            <Select value={f.card_id} onChange={(e) => set('card_id', e.target.value)} placeholder="Sem cartão vinculado"
              options={usableCards.map((c) => ({ value: c.id, label: c.label }))} />
          </Field>
        )}
        {showBank && (
          <Field label="Banco" className="span-2">
            <Select value={f.bank} onChange={(e) => set('bank', e.target.value)} placeholder="Não informar" options={meta?.banks ?? []} />
          </Field>
        )}
        {preview && (
          <div className={`invoice-hint span-2 ${preview.is_open ? '' : 'next'}`}>
            Cai na fatura de <b>{preview.invoice_label}</b> (vence dia {preview.due_day}).{' '}
            {preview.is_open && preview.days_to_closing !== null && `Fecha em ${preview.days_to_closing} dia(s). `}
            {preview.credit_limit && preview.projected_usage !== null && (
              <>Uso do limite{preview.is_shared_limit ? ' compartilhado' : ''}: {brl(preview.projected_usage)} de {brl(preview.credit_limit)}.</>
            )}
          </div>
        )}
        {!editing && (
          <div className="span-2">
            <Segmented ariaLabel="Tipo de lançamento" value={f.mode} onChange={(v) => set('mode', v)}
              options={[{ value: 'avista', label: 'Única' },
                ...(f.payment_method === CREDIT ? [{ value: 'parcelado' as const, label: 'Parcelada' }] : []),
                { value: 'recorrente', label: 'Recorrente' }]} />
          </div>
        )}
        {f.mode === 'parcelado' && (
          <Field label="Parcelas">
            <Select value={f.installments} onChange={(e) => set('installments', e.target.value)}
              options={Array.from({ length: 23 }, (_, i) => ({ value: i + 2, label: `${i + 2}×` }))} />
          </Field>
        )}
        {f.mode === 'recorrente' && (
          <Field label="Repetir por (meses)">
            <Input type="number" min={2} max={120} value={f.recurring_months} onChange={(e) => set('recurring_months', e.target.value)} />
          </Field>
        )}
        <div className="span-2 checks">
          <Check label="Já está pago" checked={f.paid} onChange={(v) => set('paid', v)}
            hint={f.mode !== 'avista' ? 'Vale só para a primeira ocorrência' : undefined} />
          {editing && expense?.series && (
            <Check label={`Aplicar a todas as ${expense.series.type === 'parcelado' ? 'parcelas' : 'recorrências'}`}
              checked={f.apply_to_group} onChange={(v) => set('apply_to_group', v)}
              hint="Descrição, categoria, pagamento e cartão" />
          )}
        </div>
        {editing && expense && expense.month !== expense.purchase_month && (
          <p className="span-2 muted small">Competência atual: fatura de {String(expense.month).padStart(2, '0')}/{expense.year}. Comprado em {dateBR(expense.purchase_date)}.</p>
        )}
        {error && <p className="span-2 form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}
