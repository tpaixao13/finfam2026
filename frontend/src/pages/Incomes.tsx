import { useState } from 'react'
import { Badge, Button, Check, ConfirmButton, Empty, ErrorBox, Field, IconButton, Input, Modal, Money, MonthNav,
  PageHeader, Panel, Segmented, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { dateBR, parseMoney } from '../lib/format'
import { useApi, useFormState, usePeriod } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import type { Income } from '../lib/types'
import { BankBalancesPanel } from './BankBalances'

interface IncomesData {
  fixed: Income[]; variable: Income[]; fixed_total: number; variable_total: number
  received_total: number; pending_total: number
}

type Kind = 'fixa' | 'variavel'

interface FormState {
  user_id: string; amount: string; company: string; income_type: Kind; recurring: boolean
  recurring_months: string; payment_day_type: '' | 'fixo' | 'util'; payment_day: string; received: boolean
  update_forward: boolean
}

function IncomeForm({ open, onClose, editing, month, year, onSaved }:
  { open: boolean; onClose: () => void; editing: Income | null; month: number; year: number; onSaved: () => void }) {
  const { me, meta, toast } = useSession()
  const blank: FormState = { user_id: String(me?.user.id ?? ''), amount: '', company: '', income_type: 'fixa', recurring: false,
    recurring_months: '12', payment_day_type: '', payment_day: '', received: false, update_forward: false }
  const { f, set, busy, error, setError, run } = useFormState<FormState>(open, () => editing
    ? { ...blank, user_id: String(editing.user_id), amount: String(editing.amount).replace('.', ','),
      company: editing.company ?? '', income_type: editing.income_type, payment_day_type: editing.payment_day_type ?? '',
      payment_day: editing.payment_day ? String(editing.payment_day) : '', received: editing.received }
    : blank)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const amount = parseMoney(f.amount)
    if (!Number.isFinite(amount) || amount <= 0) { setError('Informe um valor maior que zero.'); return }
    const payDay = f.payment_day_type ? { payment_day_type: f.payment_day_type, payment_day: Number(f.payment_day) || null } : { payment_day_type: null }
    await run(async () => {
      const r = editing
        ? await api.put<{ message: string }>(`/incomes/${editing.id}`, { amount: amount.toFixed(2), company: f.company || null, update_forward: f.update_forward, ...payDay })
        : await api.post<{ message: string }>('/incomes', {
          user_id: Number(f.user_id), amount: amount.toFixed(2), company: f.company || null, income_type: f.income_type,
          month, year, received: f.received, recurring_months: f.income_type === 'fixa' && f.recurring ? Number(f.recurring_months) : null, ...payDay })
      toast(r.message)
      onSaved()
      onClose()
    })
  }

  return (
    <Modal open={open} onClose={onClose} title={editing ? 'Editar renda' : 'Nova renda'}
      footer={<><Button variant="ghost" onClick={onClose}>Cancelar</Button><Button type="submit" form="income-form" busy={busy}>Salvar</Button></>}>
      <form id="income-form" className="form-grid" onSubmit={submit}>
        {!editing && (
          <div className="span-2">
            <Segmented ariaLabel="Tipo de renda" value={f.income_type} onChange={(v) => set('income_type', v)}
              options={[{ value: 'fixa', label: 'Fixa (salário)' }, { value: 'variavel', label: 'Variável' }]} />
          </div>
        )}
        {!editing && (
          <Field label="Pessoa">
            <Select value={f.user_id} onChange={(e) => set('user_id', e.target.value)} options={(me?.members ?? []).map((u) => ({ value: String(u.id), label: u.name }))} />
          </Field>
        )}
        <Field label="Valor (R$)"><Input inputMode="decimal" value={f.amount} onChange={(e) => set('amount', e.target.value)} required placeholder="0,00" /></Field>
        <Field label={f.income_type === 'fixa' ? 'Empresa' : 'Origem'} className={editing ? '' : 'span-2'}>
          {f.income_type === 'variavel' && !editing
            ? <Select value={f.company} onChange={(e) => set('company', e.target.value)} placeholder="Escolha" options={meta?.variable_income_sources ?? []} />
            : <Input value={f.company} onChange={(e) => set('company', e.target.value)} maxLength={200} />}
        </Field>
        <Field label="Dia de pagamento">
          <Select value={f.payment_day_type} onChange={(e) => set('payment_day_type', e.target.value as FormState['payment_day_type'])}
            placeholder="Sem data definida" options={[{ value: 'fixo', label: 'Dia fixo do mês' }, { value: 'util', label: 'N-ésimo dia útil' }]} />
        </Field>
        {f.payment_day_type && (
          <Field label={f.payment_day_type === 'util' ? 'Qual dia útil (1–22)' : 'Dia (1–31)'}>
            <Input type="number" min={1} max={f.payment_day_type === 'util' ? 22 : 31} value={f.payment_day} onChange={(e) => set('payment_day', e.target.value)} required />
          </Field>
        )}
        <div className="span-2 checks">
          {!editing && f.income_type === 'fixa' && <Check label="Repetir nos próximos meses" checked={f.recurring} onChange={(v) => set('recurring', v)} />}
          {!editing && <Check label="Já recebida" checked={f.received} onChange={(v) => set('received', v)} />}
          {editing?.group_id && <Check label="Aplicar também aos meses seguintes" checked={f.update_forward} onChange={(v) => set('update_forward', v)} />}
        </div>
        {!editing && f.income_type === 'fixa' && f.recurring && (
          <Field label="Por quantos meses">
            <Select value={f.recurring_months} onChange={(e) => set('recurring_months', e.target.value)}
              options={Array.from({ length: 23 }, (_, i) => ({ value: String(i + 2), label: `${i + 2} meses` }))} />
          </Field>
        )}
        {error && <p className="span-2 form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

function IncomeRows({ items, onChange, onEdit }: { items: Income[]; onChange: () => void; onEdit: (i: Income) => void }) {
  const act = useAction()
  const { bumpData } = useSession()
  const refresh = () => { onChange(); bumpData() }
  if (items.length === 0) return <Empty icon="cash-coin" title="Nenhuma renda lançada." />
  return (
    <ul className="tx-list">
      {items.map((s) => (
        <li key={s.id} className="tx">
          <div className="tx-main">
            <span className="tx-desc">{s.company || (s.income_type === 'fixa' ? 'Salário' : 'Renda variável')}{s.group_id && <span className="tx-series">↻</span>}</span>
            <span className="tx-meta">{s.user_name}{s.expected_date ? ` · previsto para ${dateBR(s.expected_date)}` : ''}</span>
          </div>
          <Money value={s.amount} tone="income" className="tx-amount" />
          <button className="tx-status" onClick={() => act(() => api.post<{ message: string }>(`/incomes/${s.id}/toggle-received`), refresh)}
            title={s.received ? 'Marcar como a receber' : 'Marcar como recebida'}
            aria-label={`${s.received ? 'Recebida' : 'A receber'} — ${s.received ? 'marcar como a receber' : 'marcar como recebida'}`}>
            <Badge tone={s.received ? 'success' : 'warning'}>{s.received ? 'Recebida' : 'A receber'}</Badge>
          </button>
          <span className="tx-actions">
            <IconButton icon="pencil" label="Editar" onClick={() => onEdit(s)} />
            <ConfirmButton label="Excluir" confirmLabel="Excluir" onConfirm={() => act(() => api.del<{ message: string }>(`/incomes/${s.id}`), refresh)} />
          </span>
        </li>
      ))}
    </ul>
  )
}

export function IncomesPage() {
  const { month, year, setPeriod } = usePeriod()
  const { dataVersion } = useSession()
  const data = useApi(() => api.get<IncomesData>('/incomes', { month, year }), [month, year, dataVersion])
  const [form, setForm] = useState<{ open: boolean; editing: Income | null }>({ open: false, editing: null })
  const d = data.data

  return (
    <div className="page">
      <PageHeader title="Rendas" subtitle={<MonthNav month={month} year={year} onChange={setPeriod} />}
        actions={<Button icon="plus" onClick={() => setForm({ open: true, editing: null })}>Nova renda</Button>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : (
        <>
          <dl className="kpis kpis-inline">
            <div><dt>Recebido</dt><dd><Money value={d.received_total} tone="income" /></dd></div>
            <div><dt>A receber</dt><dd><Money value={d.pending_total} /></dd></div>
            <div><dt>Total do mês</dt><dd><Money value={d.fixed_total + d.variable_total} /></dd></div>
          </dl>
          <BankBalancesPanel />
          <div className="grid-2">
            <Panel title={<>Fixas <Money value={d.fixed_total} /></>} flush>
              <IncomeRows items={d.fixed} onChange={data.reload} onEdit={(i) => setForm({ open: true, editing: i })} />
            </Panel>
            <Panel title={<>Variáveis <Money value={d.variable_total} /></>} flush>
              <IncomeRows items={d.variable} onChange={data.reload} onEdit={(i) => setForm({ open: true, editing: i })} />
            </Panel>
          </div>
        </>
      )}
      <IncomeForm open={form.open} editing={form.editing} month={month} year={year} onSaved={data.reload}
        onClose={() => setForm({ open: false, editing: null })} />
    </div>
  )
}
