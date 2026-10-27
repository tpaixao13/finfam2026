// Open Finance — etapa 3: o que chegou do banco espera aqui. Só vira despesa o que a pessoa aprovar.
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Empty, ErrorBox, Input, PageHeader, Panel, Segmented, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl, dayMonth } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction } from '../lib/session'

type Status = 'pending' | 'duplicate' | 'ignored' | 'imported'
interface BankTx {
  id: number; date: string; description: string; amount: number; status: Status; reason: string | null
  installment: { number: number; total: number } | null; bank_pending: boolean; bill: string | null
  bank_name: string | null; account_type: 'BANK' | 'CREDIT'; account_name: string | null
  account_last_digits: string | null; card_id: number | null
  expense: { id: number; description: string; amount: number; date: string } | null
  category?: string; payment_method?: string
}
interface TxList { counts: Record<Status, number>; categories: string[]; items: BankTx[] }
interface Draft { selected: boolean; category: string; description: string; payment_method: string }

const TABS: { value: Status; label: string }[] = [
  { value: 'pending', label: 'Para revisar' }, { value: 'duplicate', label: 'Já no FinFam' },
  { value: 'ignored', label: 'Ignorados' }, { value: 'imported', label: 'Lançados' },
]
const EMPTY: Record<Status, string> = {
  pending: 'Nada para revisar agora',
  duplicate: 'Nenhum lançamento repetido',
  ignored: 'Nenhum lançamento ignorado',
  imported: 'Nenhum lançamento aprovado ainda',
}

/** Linha de origem: data · banco · conta/cartão · parcela. */
function Source({ tx }: { tx: BankTx }) {
  const where = tx.account_type === 'CREDIT' ? 'Cartão' : 'Conta'
  return (
    <span className="muted small">
      {dayMonth(tx.date)} · {tx.bank_name ?? 'Banco'} · {where}{tx.account_last_digits ? ` •••• ${tx.account_last_digits}` : ''}
      {tx.installment && ` · parcela ${tx.installment.number}/${tx.installment.total}`}
      {tx.bill && ` · fatura ${tx.bill}`}
      {tx.bank_pending && ' · ainda pendente no banco'}
    </span>
  )
}

function PendingRow({ tx, draft, onChange }:
  { tx: BankTx; draft: Draft; onChange: (d: Partial<Draft>) => void }) {
  const credit = tx.account_type === 'CREDIT'
  const parcelado = credit && tx.installment && tx.installment.number <= 1
  return (
    <li className={`review-row ${draft.selected ? 'on' : ''}`}>
      <input type="checkbox" className="review-pick" checked={draft.selected} aria-label={`Selecionar ${tx.description}`}
        onChange={(e) => onChange({ selected: e.target.checked })} />
      <div className="review-main">
        <Input value={draft.description} aria-label="Descrição" maxLength={200}
          onChange={(e) => onChange({ description: e.target.value })} />
        <Source tx={tx} />
        {parcelado && <span className="small">Vai lançar as {tx.installment!.total} parcelas, como no site.</span>}
        {credit && !tx.card_id && <span className="small warn-text">Cartão do banco não ligado: vai sem cartão do FinFam.</span>}
      </div>
      <div className="review-fields">
        <Input aria-label="Categoria" list="review-cats" value={draft.category} maxLength={50} required
          placeholder="Categoria" title="Escolha da lista ou digite uma categoria nova"
          onChange={(e) => onChange({ category: e.target.value })} />
        {credit ? <span className="muted small review-method">Cartão de Crédito</span> : (
          <Select aria-label="Forma de pagamento" value={draft.payment_method} options={['PIX', 'Cartão de Débito']}
            onChange={(e) => onChange({ payment_method: e.target.value })} />
        )}
      </div>
      <b className="review-amount">{brl(tx.amount)}</b>
    </li>
  )
}

function DoneRow({ tx, onRestore }: { tx: BankTx; onRestore?: () => void }) {
  return (
    <li className="review-row done">
      <div className="review-main">
        <span>{tx.description}</span>
        <Source tx={tx} />
        {tx.status === 'duplicate' && tx.expense && (
          <span className="small">Igual a <b>{tx.expense.description}</b> de {dayMonth(tx.expense.date)} ({brl(tx.expense.amount)})</span>
        )}
        {tx.status === 'ignored' && tx.reason && <span className="small">{tx.reason}</span>}
      </div>
      <b className="review-amount">{brl(tx.amount)}</b>
      {onRestore && <Button variant="ghost" onClick={onRestore}>Revisar</Button>}
    </li>
  )
}

export function ReviewImportsPage() {
  const [tab, setTab] = useState<Status>('pending')
  const data = useApi(() => api.get<TxList>(`/pluggy/transactions?status=${tab}`), [tab])
  const act = useAction()
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [busy, setBusy] = useState(false)
  const list = data.data

  useEffect(() => {
    if (!list || tab !== 'pending') return
    setDrafts(Object.fromEntries(list.items.map((t) => [t.id, {
      selected: true, category: t.category ?? 'Outros', description: t.description, payment_method: t.payment_method ?? 'PIX',
    }])))
  }, [list, tab])

  const picked = useMemo(() => (list?.items ?? []).filter((t) => drafts[t.id]?.selected), [list, drafts])
  const all = !!list?.items.length && picked.length === list.items.length
  const edit = (id: number, d: Partial<Draft>) => setDrafts((s) => ({ ...s, [id]: { ...s[id], ...d } }))
  const run = async (fn: () => Promise<{ message: string }>) => { setBusy(true); await act(fn, data.reload); setBusy(false) }

  const [progress, setProgress] = useState('')
  // em lotes de 100 (o servidor aceita até 200 por vez), dos mais antigos para os mais novos:
  // a parcela mais antiga cria a série e as seguintes, em outro lote, são reconhecidas
  const approve = () => run(async () => {
    const items = [...picked].sort((a, b) => a.date.localeCompare(b.date)).map((t) => {
      const d = drafts[t.id]
      return { id: t.id, category: d.category.trim() || 'Outros', description: d.description.trim() || t.description,
        ...(t.account_type === 'BANK' ? { payment_method: d.payment_method } : {}) }
    })
    let imported = 0, already = 0
    try {
      for (let i = 0; i < items.length; i += 100) {
        setProgress(`${Math.min(i + 100, items.length)}/${items.length}`)
        const r = await api.post<{ imported: number; already?: number }>('/pluggy/transactions/approve', { items: items.slice(i, i + 100) })
        imported += r.imported
        already += r.already ?? 0
      }
    } finally { setProgress('') }
    return { message: `${imported} despesa(s) lançada(s) no FinFam.` + (already ? ` ${already} já estava(m) no FinFam.` : '') }
  })
  const ignore = () => run(async () => {
    const ids = picked.map((t) => t.id)
    for (let i = 0; i < ids.length; i += 400) await api.post('/pluggy/transactions/ignore', { ids: ids.slice(i, i + 400) })
    return { message: `${ids.length} lançamento(s) ignorado(s).` }
  })
  const restore = (id: number) => run(() => api.post('/pluggy/transactions/restore', { ids: [id] }))
  const total = picked.reduce((s, t) => s + t.amount, 0)

  return (
    <div className="page">
      <PageHeader title="Revisar importados"
        subtitle="O que chegou dos seus bancos. Só vira despesa o que você aprovar."
        actions={<Link to="/bank" className="btn btn-ghost">Contas conectadas</Link>} />
      <Segmented ariaLabel="Situação" value={tab} onChange={setTab}
        options={TABS.map((t) => ({ value: t.value, label: `${t.label}${list ? ` (${list.counts[t.value]})` : ''}` }))} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !list ? <Spinner /> : !list.items.length ? (
        <Panel><Empty icon="check-all" title={EMPTY[tab]}>
          {tab === 'pending' && 'Quando o banco mandar compras novas, elas aparecem aqui.'}
        </Empty></Panel>
      ) : tab === 'pending' ? (
        <Panel flush>
          <div className="review-bar">
            <label className="check">
              <input type="checkbox" checked={all}
                onChange={(e) => setDrafts((s) => Object.fromEntries(Object.entries(s).map(([k, d]) => [k, { ...d, selected: e.target.checked }])))} />
              <span>{picked.length} de {list.items.length} · {brl(total)}</span>
            </label>
            <div className="row gap">
              <Button variant="ghost" icon="x" disabled={!picked.length || busy} onClick={ignore}>Ignorar</Button>
              <Button icon="check2" busy={busy} disabled={!picked.length} onClick={approve}>{progress ? `Lançando ${progress}…` : `Lançar ${picked.length || ''}`}</Button>
            </div>
          </div>
          <datalist id="review-cats">{list.categories.map((c) => <option key={c} value={c} />)}</datalist>
          <ul className="review-list">
            {list.items.map((t) => drafts[t.id] && (
              <PendingRow key={t.id} tx={t} draft={drafts[t.id]} onChange={(d) => edit(t.id, d)} />
            ))}
          </ul>
        </Panel>
      ) : (
        <Panel flush>
          <ul className="review-list">
            {list.items.map((t) => <DoneRow key={t.id} tx={t}
              onRestore={tab === 'duplicate' || tab === 'ignored' ? () => restore(t.id) : undefined} />)}
          </ul>
        </Panel>
      )}
      {tab === 'imported' && list && list.items.length > 0 && (
        <p className="muted small"><Badge tone="info">Dica</Badge> Para mudar algo já lançado, edite a despesa em Despesas.</p>
      )}
    </div>
  )
}
