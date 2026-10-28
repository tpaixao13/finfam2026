import { BarChart, Donut } from '../components/charts'
import { ExpenseList } from '../components/ExpenseList'
import { Icon } from '../components/Icon'
import { Avatar, Button, Delta, Empty, ErrorBox, Money, MonthNav, Panel, Progress, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl } from '../lib/format'
import { useApi, usePeriod } from '../lib/hooks'
import { Link, navigate } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Expense, Goal, Insight, User } from '../lib/types'

interface DashboardData {
  month: number; year: number; month_name: string
  kpis: { income: number; expenses: number; credit: number; balance: number
    deltas: { income: number | null; expenses: number | null; credit: number | null; balance: number | null } }
  people: { user: User; income: number; expenses: number; balance: number }[]
  insights: Insight[]; goals: Goal[]; pending: Expense[]; pending_total: number; recent: Expense[]
}
interface Categories { labels: string[]; data: number[]; colors: string[] }
interface Monthly { labels: string[]; expenses: number[]; income: number[] }

export function InsightItem({ ins }: { ins: Insight }) {
  const body = (
    <>
      <span className="insight-icon"><Icon name={ins.icon} /></span>
      <span className="insight-text"><b>{ins.title}</b><span>{ins.message}</span></span>
      {ins.value && <span className="insight-value">{ins.value}</span>}
    </>
  )
  return ins.link
    ? <Link to={ins.link} className={`insight insight-${ins.type}`}>{body}</Link>
    : <div className={`insight insight-${ins.type}`}>{body}</div>
}

export function GoalMini({ g }: { g: Goal }) {
  const tone = g.status_color === 'danger' ? 'danger' : g.status_color === 'warning' ? 'warning' : 'success'
  return (
    <div className="goal-mini">
      <div className="goal-mini-head"><span>{g.emoji} {g.title}</span>
        <b>{g.type === 'spending_limit' ? `${Math.round(g.pct_used)}% usado` : `${Math.round(g.pct)}%`}</b></div>
      <Progress value={g.type === 'spending_limit' ? g.pct_used : g.pct} tone={tone} />
      <small className="muted">{g.coaching}</small>
    </div>
  )
}

export function DashboardPage() {
  const { month, year, setPeriod } = usePeriod()
  const { dataVersion, openExpense } = useSession()
  const act = useAction()
  const dash = useApi(() => api.get<DashboardData>('/dashboard', { month, year }), [month, year, dataVersion])
  const cats = useApi(() => api.get<Categories>('/charts/categories', { month, year }), [month, year, dataVersion])
  const monthly = useApi(() => api.get<Monthly>('/charts/monthly', { month, year, months: 6 }), [month, year, dataVersion])

  if (dash.error) return <ErrorBox message={dash.error} onRetry={dash.reload} />
  const d = dash.data
  const reload = () => { dash.reload(); cats.reload(); monthly.reload() }

  return (
    <div className="page">
      <div className="hero">
        <div>
          <MonthNav month={month} year={year} onChange={setPeriod} />
          {d && (
            <div className="hero-balance">
              <span className="hero-label">Sobra do mês</span>
              <Money value={d.kpis.balance} tone="auto" className="hero-number" />
              <Delta value={d.kpis.deltas.balance} />
            </div>
          )}
        </div>
        {d && (
          <dl className="kpis">
            <div><dt>Renda recebida</dt><dd><Money value={d.kpis.income} tone="income" /></dd><Delta value={d.kpis.deltas.income} /></div>
            <div><dt>Gastos</dt><dd><Money value={d.kpis.expenses} tone="expense" /></dd><Delta value={d.kpis.deltas.expenses} inverse /></div>
            <div><dt>No cartão de crédito</dt><dd><Money value={d.kpis.credit} /></dd><Delta value={d.kpis.deltas.credit} inverse /></div>
          </dl>
        )}
      </div>

      {!d ? <Spinner /> : (
        <div className="grid-dash">
          <Panel title="Para pagar este mês" className="area-pending"
            actions={d.pending.length > 0 && (
              <Button variant="ghost" icon="check-all" onClick={() => act(
                () => api.post<{ message: string }>('/expenses/pay', { ids: d.pending.map((e) => e.id) }), reload)}>
                Pagar tudo ({brl(d.pending_total)})
              </Button>
            )}>
            {d.pending.length === 0
              ? <Empty icon="check2-circle" title="Nada pendente neste mês." />
              : <ExpenseList items={d.pending.slice(0, 8)} onChange={reload} dateField="due_date" compact />}
            {d.pending.length > 8 && <Link className="panel-link" to={`/expenses?month=${month}&year=${year}&paid=pendente`}>Ver as {d.pending.length} pendentes</Link>}
          </Panel>

          <Panel title="O que chama atenção" className="area-insights">
            {d.insights.length === 0
              ? <Empty icon="lightning" title="Sem observações ainda.">Lance despesas e rendas para ver análises.</Empty>
              : <div className="insights">{d.insights.map((ins, i) => <InsightItem key={i} ins={ins} />)}</div>}
          </Panel>

          <Panel title="Por pessoa" className="area-people">
            <ul className="people">
              {d.people.map((p) => (
                <li key={p.user.id}>
                  <Avatar name={p.user.name} url={p.user.avatar_url} color={p.user.color} />
                  <span className="people-name">{p.user.name}</span>
                  <span className="people-nums">
                    <small>renda {brl(p.income)}</small>
                    <small>gastos {brl(p.expenses)}</small>
                  </span>
                  <Money value={p.balance} tone="auto" />
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Gastos por categoria" className="area-cats">
            {cats.data && cats.data.data.length > 0
              ? <Donut labels={cats.data.labels} data={cats.data.data} colors={cats.data.colors} centerLabel="Gastos"
                  ariaLabel="Gastos por categoria" selectHint={(c) => `Ver as despesas de ${c}`}
                  onSelect={(i) => navigate(`/expenses?${new URLSearchParams({
                    category: cats.data!.labels[i], month: String(month), year: String(year) })}`)} />
              : <Empty icon="pie-chart" title="Sem gastos neste mês." />}
          </Panel>

          <Panel title="Renda x gastos — 6 meses" className="area-monthly">
            {monthly.data && (
              <BarChart ariaLabel="Renda e gastos dos últimos 6 meses" labels={monthly.data.labels} series={[
                { label: 'Renda', data: monthly.data.income, color: 'var(--income)' },
                { label: 'Gastos', data: monthly.data.expenses, color: 'var(--expense)' },
              ]} />
            )}
          </Panel>

          <Panel title="Metas" className="area-goals" actions={<Link to="/goals" className="panel-link">Ver todas</Link>}>
            {d.goals.length === 0
              ? <Empty icon="trophy" title="Nenhuma meta ativa."><Link to="/goals">Criar uma meta</Link></Empty>
              : d.goals.map((g) => <GoalMini key={g.id} g={g} />)}
          </Panel>

          <Panel title="Últimos lançamentos" className="area-recent"
            actions={<Link to={`/expenses?month=${month}&year=${year}`} className="panel-link">Ver despesas</Link>}>
            {d.recent.length === 0
              ? <Empty title="Nenhuma despesa neste mês."><Button icon="plus" onClick={() => openExpense()}>Lançar despesa</Button></Empty>
              : <ExpenseList items={d.recent} onChange={reload} compact />}
          </Panel>
        </div>
      )}
    </div>
  )
}
