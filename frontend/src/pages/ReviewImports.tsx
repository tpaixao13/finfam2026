// Open Finance — etapa 3: o que chegou do banco espera aqui. Só vira despesa o que a pessoa aprovar.
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Empty, ErrorBox, MonthNav, PageHeader, Panel, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { MONTHS } from '../lib/format'
import { useApi, usePeriod } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction } from '../lib/session'
import { DoneRow, MonthDivider, PendingRow, ReviewBar, StatusTabs, byMonth, canRestore, sumSigned, useDrafts, type Draft, type Status, type TxList } from './ReviewRows'

const TABS: Record<Status, string> = {
  pending: 'Para revisar', duplicate: 'Já no FinFam', ignored: 'Ignorados', imported: 'Lançados',
}
// "Para revisar" é a fila do que falta: mostra tudo. As outras são histórico: um mês por vez.
const EMPTY: Record<Exclude<Status, 'pending'>, string> = {
  duplicate: 'Nenhum lançamento repetido', ignored: 'Nenhum lançamento ignorado', imported: 'Nada lançado',
}

export function ReviewImportsPage() {
  const [tab, setTab] = useState<Status>('pending')
  const { month, year, setPeriod } = usePeriod()
  const query = tab === 'pending' ? `status=${tab}` : `status=${tab}&month=${month}&year=${year}`
  const data = useApi(() => api.get<TxList>(`/pluggy/transactions?${query}`).then((r) => ({ ...r, query })), [query])
  const act = useAction()
  const { drafts, setDrafts, edit, selectAll, selectSome } = useDrafts<Draft>()
  const [busy, setBusy] = useState(false)
  // ao trocar de aba, a lista anterior não fica na tela enquanto a nova carrega
  const list = data.data?.query === query ? data.data : null

  useEffect(() => {
    if (!list || tab !== 'pending') return
    // a que parece ser uma conta a pagar vem desmarcada: lançar duplicaria a conta (use "Marcar como paga")
    setDrafts(Object.fromEntries(list.items.map((t) => [t.id, {
      selected: !t.bills?.length, category: t.category ?? 'Outros', description: t.description, payment_method: t.payment_method ?? 'PIX',
    }])))
  }, [list, tab])

  const picked = useMemo(() => (list?.items ?? []).filter((t) => drafts[t.id]?.selected), [list, drafts])
  // categorias da família + as novas digitadas em qualquer linha (aparecem para todas)
  const categories = useMemo(() => {
    const all = new Set(list?.categories ?? [])
    Object.values(drafts).forEach((d) => { if (d.category.trim()) all.add(d.category.trim()) })
    return [...all].sort((a, b) => a.localeCompare(b, 'pt-BR'))
  }, [list, drafts])
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
  const payBill = (id: number, expenseId: number) => run(() => api.post(`/pluggy/transactions/${id}/pay-bill`, { expense_id: expenseId }))
  const total = sumSigned(picked)

  return (
    <div className="page">
      <PageHeader title="Revisar importados"
        subtitle="O que chegou dos seus bancos. Só vira despesa o que você aprovar."
        actions={<div className="row gap">
          <Link to="/bank/incomes" className="btn btn-ghost">Entradas</Link>
          <Link to="/bank" className="btn btn-ghost">Contas conectadas</Link>
        </div>} />
      <div className="review-toolbar">
        <StatusTabs tabs={TABS} value={tab} counts={list?.counts} onChange={setTab} />
        {tab !== 'pending' && <MonthNav month={month} year={year} onChange={setPeriod} />}
      </div>
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !list ? <Spinner /> : !list.items.length ? (
        <Panel>{tab === 'pending'
          ? <Empty icon="check-all" title="Nada para revisar agora">Quando o banco mandar compras novas, elas aparecem aqui.</Empty>
          : <Empty icon="calendar-range" title={`${EMPTY[tab]} em ${MONTHS[month - 1].toLowerCase()}`}>Use as setas para ver outro mês.</Empty>}
        </Panel>
      ) : tab === 'pending' ? (
        <Panel flush>
          <ReviewBar picked={picked.length} count={list.items.length} total={total} onToggleAll={selectAll}>
            <Button variant="ghost" icon="x" disabled={!picked.length || busy} onClick={ignore}>Ignorar</Button>
            <Button icon="check2" busy={busy} disabled={!picked.length} onClick={approve}>{progress ? `Lançando ${progress}…` : `Lançar ${picked.length || ''}`}</Button>
          </ReviewBar>
          <ul className="review-list">
            {byMonth(list.items).map(([key, items]) => [
              <MonthDivider key={key} month={key} count={items.length} picked={items.filter((t) => drafts[t.id]?.selected).length}
                total={sumSigned(items)}
                onToggle={(v) => selectSome(items.map((t) => t.id), v)} />,
              ...items.map((t) => drafts[t.id] && (
                <PendingRow key={t.id} tx={t} draft={drafts[t.id]} categories={categories} onChange={(d) => edit(t.id, d)}
                  onPayBill={(expenseId) => payBill(t.id, expenseId)} />
              )),
            ])}
          </ul>
        </Panel>
      ) : (
        <Panel flush>
          <ul className="review-list">
            {list.items.map((t) => <DoneRow key={t.id} tx={t}
              onRestore={canRestore(tab) ? () => restore(t.id) : undefined} />)}
          </ul>
        </Panel>
      )}
      {tab === 'imported' && list && list.items.length > 0 && (
        <p className="muted small"><Badge tone="info">Dica</Badge> Para mudar algo já lançado, edite a despesa em Despesas.</p>
      )}
    </div>
  )
}
