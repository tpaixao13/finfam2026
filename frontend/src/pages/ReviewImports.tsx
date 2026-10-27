// Open Finance — etapa 3: o que chegou do banco espera aqui. Só vira despesa o que a pessoa aprovar.
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Check, Empty, ErrorBox, PageHeader, Panel, Segmented, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction } from '../lib/session'
import { DoneRow, PendingRow, type Draft, type Status, type TxList } from './ReviewRows'

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

export function ReviewImportsPage() {
  const [tab, setTab] = useState<Status>('pending')
  const data = useApi(() => api.get<TxList>(`/pluggy/transactions?status=${tab}`).then((r) => ({ ...r, tab })), [tab])
  const act = useAction()
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [busy, setBusy] = useState(false)
  // ao trocar de aba, a lista anterior não fica na tela enquanto a nova carrega
  const list = data.data?.tab === tab ? data.data : null

  useEffect(() => {
    if (!list || tab !== 'pending') return
    setDrafts(Object.fromEntries(list.items.map((t) => [t.id, {
      selected: true, category: t.category ?? 'Outros', description: t.description, payment_method: t.payment_method ?? 'PIX',
    }])))
  }, [list, tab])

  const picked = useMemo(() => (list?.items ?? []).filter((t) => drafts[t.id]?.selected), [list, drafts])
  // categorias da família + as novas digitadas em qualquer linha (aparecem para todas)
  const categories = useMemo(() => {
    const all = new Set(list?.categories ?? [])
    Object.values(drafts).forEach((d) => { if (d.category.trim()) all.add(d.category.trim()) })
    return [...all].sort((a, b) => a.localeCompare(b, 'pt-BR'))
  }, [list, drafts])
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
  const total = picked.reduce((s, t) => s + (t.refund ? -t.amount : t.amount), 0)

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
            <Check checked={all} label={`${picked.length} de ${list.items.length} · ${brl(total)}`}
              onChange={(v) => setDrafts((s) => Object.fromEntries(Object.entries(s).map(([k, d]) => [k, { ...d, selected: v }])))} />
            <div className="row gap">
              <Button variant="ghost" icon="x" disabled={!picked.length || busy} onClick={ignore}>Ignorar</Button>
              <Button icon="check2" busy={busy} disabled={!picked.length} onClick={approve}>{progress ? `Lançando ${progress}…` : `Lançar ${picked.length || ''}`}</Button>
            </div>
          </div>
          <ul className="review-list">
            {list.items.map((t) => drafts[t.id] && (
              <PendingRow key={t.id} tx={t} draft={drafts[t.id]} categories={categories} onChange={(d) => edit(t.id, d)} />
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
