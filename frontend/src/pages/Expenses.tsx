import { useEffect, useState } from 'react'
import { ExpenseList } from '../components/ExpenseList'
import { Button, Empty, ErrorBox, Field, IconButton, Input, Money, PageHeader, Panel, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl, MONTHS } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link, useSearchParams } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Expense } from '../lib/types'

interface ListResponse { items: Expense[]; page: number; pages: number; total: number; total_amount: number }
interface Options { categories: string[]; banks: string[]; payment_methods: string[] }

/** Filtros que o celular esconde atrás de "Mais filtros" (busca, mês e ano ficam sempre visíveis). */
const EXTRA_FILTERS = ['user_id', 'category', 'payment_method', 'bank', 'paid'] as const
const FILTER_KEYS = ['month', 'year', 'user_id', 'category', 'payment_method', 'bank', 'paid', 'search', 'page'] as const

export function ExpensesPage() {
  const [params, setParams] = useSearchParams()
  const { me, dataVersion, openExpense } = useSession()
  const act = useAction()
  const now = new Date()
  const q: Record<string, string> = {}
  FILTER_KEYS.forEach((k) => { const v = params.get(k); if (v) q[k] = v })
  const month = q.month ?? String(now.getMonth() + 1)
  const year = q.year ?? String(now.getFullYear())
  const [search, setSearch] = useState(q.search ?? '')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [allFilters, setAllFilters] = useState(false)
  const extraActive = EXTRA_FILTERS.filter((k) => q[k]).length

  const key = JSON.stringify(q)
  const list = useApi(() => api.get<ListResponse>('/expenses', { ...q, month, year, per_page: 30 }), [key, dataVersion])
  const opts = useApi(() => api.get<Options>('/expenses/options'), [dataVersion])

  useEffect(() => { if (params.get('new') === '1') { openExpense(); setParams({ new: null }) } }, [params]) // eslint-disable-line
  useEffect(() => setSelected(new Set()), [key])
  useEffect(() => {
    const t = setTimeout(() => { if (search !== (q.search ?? '')) setParams({ search: search || null, page: null }) }, 350)
    return () => clearTimeout(t)
  }, [search]) // eslint-disable-line

  const setFilter = (k: string, v: string) => setParams({ [k]: v || null, page: null })
  const toggle = (id: number) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const data = list.data
  const late = (data?.items ?? []).filter((e) => e.status === 'vencida')

  return (
    <div className="page">
      <PageHeader title="Despesas"
        subtitle={data ? <>{data.total} lançamento(s) · total <b>{brl(data.total_amount)}</b></> : undefined}
        actions={<>
          <Button variant="ghost" icon="download" onClick={() => api.download('/expenses/export', { ...q, month, year, fmt: 'xlsx' }, 'despesas.xlsx')}>Excel</Button>
          <Button variant="ghost" icon="download" onClick={() => api.download('/expenses/export', { ...q, month, year, fmt: 'pdf' }, 'despesas.pdf')}>PDF</Button>
          <Button icon="plus" onClick={() => openExpense()}>Nova despesa</Button>
        </>} />

      <Panel className="filters">
        <div id="expense-filters" className={`filter-row ${allFilters ? 'show-all' : ''}`}>
          <Field label="Buscar"><Input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Descrição" /></Field>
          <Field label="Mês">
            <Select value={month} onChange={(e) => setFilter('month', e.target.value)}
              options={[{ value: '0', label: 'Ano inteiro' }, ...MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))]} />
          </Field>
          <Field label="Ano"><Input type="number" min={2000} max={2100} value={year} onChange={(e) => setFilter('year', e.target.value)} /></Field>
          <Field label="Pessoa">
            <Select value={q.user_id ?? ''} onChange={(e) => setFilter('user_id', e.target.value)} placeholder="Todas"
              options={(me?.members ?? []).map((u) => ({ value: String(u.id), label: u.name }))} />
          </Field>
          <Field label="Categoria">
            <Select value={q.category ?? ''} onChange={(e) => setFilter('category', e.target.value)} placeholder="Todas" options={opts.data?.categories ?? []} />
          </Field>
          <Field label="Pagamento">
            <Select value={q.payment_method ?? ''} onChange={(e) => setFilter('payment_method', e.target.value)} placeholder="Todos" options={opts.data?.payment_methods ?? []} />
          </Field>
          <Field label="Banco">
            <Select value={q.bank ?? ''} onChange={(e) => setFilter('bank', e.target.value)} placeholder="Todos" options={opts.data?.banks ?? []} />
          </Field>
          <Field label="Situação">
            <Select value={q.paid ?? ''} onChange={(e) => setFilter('paid', e.target.value)} placeholder="Todas"
              options={[{ value: 'pendente', label: 'Pendentes' }, { value: 'pago', label: 'Pagas' }]} />
          </Field>
        </div>
        <button type="button" className="link-btn filters-toggle" aria-expanded={allFilters} aria-controls="expense-filters"
          onClick={() => setAllFilters((v) => !v)}>
          {allFilters ? 'Menos filtros' : `Mais filtros${extraActive ? ` (${extraActive} ativo${extraActive > 1 ? 's' : ''})` : ''}`}
        </button>
      </Panel>

      {selected.size === 0 && late.length > 0 && (
        <p className="small muted late-shortcut">
          <button type="button" className="link-btn" onClick={() => setSelected(new Set(late.map((e) => e.id)))}>
            Selecionar as {late.length} vencida(s) desta página
          </button>
          <span>Todas as atrasadas de uma vez: <Link to="/bills">Contas a pagar › Pagar todas</Link></span>
        </p>
      )}
      {selected.size > 0 && (
        <div className="bulk-bar">
          <span>{selected.size} selecionada(s)</span>
          <Button variant="success" icon="check-all" onClick={() => act(
            () => api.post<{ message: string }>('/expenses/pay', { ids: [...selected] }), () => { setSelected(new Set()); list.reload() })}>
            Marcar como pagas
          </Button>
          <Button variant="ghost" onClick={() => setSelected(new Set())}>Limpar seleção</Button>
        </div>
      )}

      <Panel flush>
        {list.error ? <ErrorBox message={list.error} onRetry={list.reload} />
          : !data ? <Spinner />
          : data.items.length === 0
            ? <Empty title="Nenhuma despesa com esses filtros."><Button icon="plus" onClick={() => openExpense()}>Lançar despesa</Button></Empty>
            : <ExpenseList items={data.items} onChange={list.reload} selectable selected={selected} onToggleSelect={toggle} />}
      </Panel>

      {data && data.pages > 1 && (
        <nav className="pager" aria-label="Paginação">
          <IconButton icon="chevron-left" label="Página anterior" disabled={data.page <= 1} onClick={() => setParams({ page: data.page - 1 })} />
          <span>Página {data.page} de {data.pages}</span>
          <IconButton icon="chevron-right" label="Próxima página" disabled={data.page >= data.pages} onClick={() => setParams({ page: data.page + 1 })} />
        </nav>
      )}
    </div>
  )
}

interface Bills {
  date: string; overdue: Expense[]; today: Expense[]; upcoming: Expense[]
  totals: { overdue: number; today: number; upcoming: number }; total: number
}

export function BillsPage() {
  const { dataVersion } = useSession()
  const act = useAction()
  const [days, setDays] = useState(7)
  const bills = useApi(() => api.get<Bills>('/expenses/bills', { days_ahead: days }), [days, dataVersion])
  const b = bills.data
  const payAll = (items: Expense[]) => act(() => api.post<{ message: string }>('/expenses/pay', { ids: items.map((e) => e.id) }), bills.reload)

  const section = (title: string, items: Expense[], total: number, tone: string) => (
    <Panel title={<span className={`bills-title tone-${tone}`}>{title} <Money value={total} /></span>}
      actions={items.length > 1 && <Button variant="ghost" icon="check-all" onClick={() => payAll(items)}>Pagar todas</Button>}>
      {items.length === 0 ? <Empty icon="check2-circle" title="Nada aqui." /> : <ExpenseList items={items} onChange={bills.reload} dateField="due_date" />}
    </Panel>
  )

  return (
    <div className="page">
      <PageHeader title="Contas a pagar" subtitle="Pelo vencimento real: compras no crédito vencem no dia da fatura."
        actions={<Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))} aria-label="Período"
          options={[{ value: '7', label: 'Próximos 7 dias' }, { value: '15', label: 'Próximos 15 dias' }, { value: '30', label: 'Próximos 30 dias' }]} />} />
      {bills.error ? <ErrorBox message={bills.error} onRetry={bills.reload} /> : !b ? <Spinner /> : (
        <div className="stack-lg">
          {section('Em atraso', b.overdue, b.totals.overdue, 'danger')}
          {section('Vencem hoje', b.today, b.totals.today, 'warning')}
          {section(`Próximos ${days} dias`, b.upcoming, b.totals.upcoming, 'neutral')}
        </div>
      )}
    </div>
  )
}
