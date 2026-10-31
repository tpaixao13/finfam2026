// Open Finance — entradas (PIX recebido, salário, rendimento) que chegaram do banco.
// Só vira renda o que a pessoa aprovar; transferência entre as contas dela já vem ignorada.
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Check, Empty, ErrorBox, Input, Money, PageHeader, Panel, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl, dayMonth } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction } from '../lib/session'
import { ReviewBar, StatusTabs, canRestore, useDrafts, type Status } from './ReviewRows'

interface Suggestion {
  label: string; income_type: IncomeType; company: string; recurring: boolean; own_transfer_hint: boolean
  expected: { id: number; company: string | null; amount: number } | null
}
interface BankIncome {
  id: number; date: string; description: string; amount: number; counterpart: string | null; status: Status
  reason: string | null; bank_name: string; account_name: string | null; account_last_digits: string | null
  owner_name: string | null; suggestion?: Suggestion
  salary: { id: number; company: string | null; amount: number; month: number; year: number } | null
}
interface IncomeList { counts: Record<Status, number>; items: BankIncome[] }
type IncomeType = 'fixa' | 'variavel'
interface Draft { selected: boolean; income_type: IncomeType; company: string; use_expected: boolean }

const TABS: Record<Status, string> = {
  pending: 'Para revisar', duplicate: 'Já em Rendas', ignored: 'Ignoradas', imported: 'Lançadas',
}
const EMPTY: Record<Status, string> = {
  pending: 'Nenhuma entrada para revisar', duplicate: 'Nenhuma entrada repetida',
  ignored: 'Nenhuma entrada ignorada', imported: 'Nenhuma entrada lançada ainda',
}
const TYPES = [{ value: 'fixa', label: 'Renda fixa' }, { value: 'variavel', label: 'Renda variável' }]

function Source({ inc }: { inc: BankIncome }) {
  return (
    <span className="muted small">
      {dayMonth(inc.date)} · {inc.bank_name}{inc.owner_name && ` (de ${inc.owner_name})`}
      {inc.account_last_digits && ` •••• ${inc.account_last_digits}`} · {inc.description}
    </span>
  )
}

function PendingIncome({ inc, draft, onChange }: { inc: BankIncome; draft: Draft; onChange: (d: Partial<Draft>) => void }) {
  const sug = inc.suggestion!
  const marks = !!sug.expected && draft.use_expected
  return (
    <li className={`review-row ${draft.selected ? 'on' : ''}`}>
      <input type="checkbox" className="review-pick" checked={draft.selected} aria-label={`Selecionar ${inc.description}`}
        onChange={(e) => onChange({ selected: e.target.checked })} />
      <div className="review-main">
        <Input value={draft.company} aria-label={`Fonte de ${inc.description}`} maxLength={200} disabled={marks}
          onChange={(e) => onChange({ company: e.target.value })} />
        <Source inc={inc} />
        <span className="small"><Badge tone={sug.own_transfer_hint ? 'warning' : 'info'}>{sug.label}</Badge>
          {sug.recurring && ' Parecida com a do mês passado: sugerida como renda fixa.'}
          {sug.own_transfer_hint && ' Parece dinheiro seu vindo de outra conta: se for, ignore.'}</span>
        {sug.expected && (
          <Check checked={draft.use_expected} onChange={(v) => onChange({ use_expected: v })}
            label={`Marcar a renda prevista "${sug.expected.company ?? 'sem nome'}" (${brl(sug.expected.amount)}) como recebida, com o valor do banco`} />
        )}
      </div>
      <div className="review-fields">
        <Select aria-label={`Tipo de renda de ${inc.description}`} value={draft.income_type} options={TYPES} disabled={marks}
          onChange={(e) => onChange({ income_type: e.target.value as IncomeType })} />
      </div>
      <Money value={inc.amount} tone="income" className="review-amount" />
    </li>
  )
}

function DoneIncome({ inc, onRestore }: { inc: BankIncome; onRestore?: () => void }) {
  return (
    <li className="review-row done">
      <div className="review-main">
        <span>{inc.counterpart ?? inc.description}</span>
        <Source inc={inc} />
        {inc.salary && <span className="small">Em Rendas: <b>{inc.salary.company ?? 'sem nome'}</b> ({brl(inc.salary.amount)}, {String(inc.salary.month).padStart(2, '0')}/{inc.salary.year})</span>}
        {inc.status === 'ignored' && inc.reason && <span className="small">{inc.reason}</span>}
      </div>
      <Money value={inc.amount} tone="income" className="review-amount" />
      {onRestore && <Button variant="ghost" aria-label={`Revisar ${inc.description} de novo`} onClick={onRestore}>Revisar</Button>}
    </li>
  )
}

export function ReviewIncomesPage() {
  const [tab, setTab] = useState<Status>('pending')
  const data = useApi(() => api.get<IncomeList>(`/pluggy/incomes?status=${tab}`).then((r) => ({ ...r, tab })), [tab])
  const act = useAction()
  const { drafts, setDrafts, edit, selectAll } = useDrafts<Draft>()
  const [busy, setBusy] = useState(false)
  const list = data.data?.tab === tab ? data.data : null

  useEffect(() => {
    if (!list || tab !== 'pending') return
    setDrafts(Object.fromEntries(list.items.map((i) => [i.id, {
      selected: !i.suggestion?.own_transfer_hint, income_type: i.suggestion?.income_type ?? 'variavel',
      company: i.suggestion?.company ?? i.description, use_expected: true,
    }])))
  }, [list, tab])

  const picked = useMemo(() => (list?.items ?? []).filter((i) => drafts[i.id]?.selected), [list, drafts])
  const run = async (fn: () => Promise<{ message: string }>) => { setBusy(true); await act(fn, data.reload); setBusy(false) }
  const approve = () => run(() => api.post('/pluggy/incomes/approve', { items: picked.map((i) => {
    const d = drafts[i.id]
    return { id: i.id, income_type: d.income_type, company: d.company.trim() || null, use_expected: d.use_expected }
  }) }))
  const ignore = () => run(() => api.post('/pluggy/incomes/ignore', { ids: picked.map((i) => i.id) }))
  const restore = (id: number) => run(() => api.post('/pluggy/incomes/restore', { ids: [id] }))
  const total = picked.reduce((s, i) => s + i.amount, 0)

  return (
    <div className="page">
      <PageHeader title="Entradas do banco"
        subtitle="PIX recebido, salário e rendimentos que chegaram dos bancos. Só vira renda o que você aprovar."
        actions={<div className="row gap">
          <Link to="/bank/review" className="btn btn-ghost">Saídas</Link>
          <Link to="/incomes" className="btn btn-ghost">Rendas</Link>
        </div>} />
      <StatusTabs tabs={TABS} value={tab} counts={list?.counts} onChange={setTab} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !list ? <Spinner /> : !list.items.length ? (
        <Panel><Empty icon="check-all" title={EMPTY[tab]}>
          {tab === 'pending' && 'Quando entrar dinheiro na conta, aparece aqui. Transferência entre as suas contas e resgate de investimento ficam em Ignoradas.'}
        </Empty></Panel>
      ) : tab === 'pending' ? (
        <Panel flush>
          <ReviewBar picked={picked.length} count={list.items.length} total={total} onToggleAll={selectAll}>
            <Button variant="ghost" icon="x" disabled={!picked.length || busy} onClick={ignore}>Ignorar</Button>
            <Button icon="check2" busy={busy} disabled={!picked.length} onClick={approve}>Lançar em Rendas {picked.length || ''}</Button>
          </ReviewBar>
          <ul className="review-list">
            {list.items.map((i) => drafts[i.id] && <PendingIncome key={i.id} inc={i} draft={drafts[i.id]} onChange={(d) => edit(i.id, d)} />)}
          </ul>
        </Panel>
      ) : (
        <Panel flush>
          <ul className="review-list">
            {list.items.map((i) => <DoneIncome key={i.id} inc={i}
              onRestore={canRestore(tab) ? () => restore(i.id) : undefined} />)}
          </ul>
        </Panel>
      )}
    </div>
  )
}
