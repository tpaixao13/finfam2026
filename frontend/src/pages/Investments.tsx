import { useState } from 'react'
import { LineChart } from '../components/charts'
import { ConfirmButton, Empty, ErrorBox, Money, PageHeader, Panel, Spinner, Table, Button } from '../components/ui'
import { api } from '../lib/api'
import { brl, money, MONTHS_SHORT, pct, rate } from '../lib/format'
import { useApi } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import type { Investment } from '../lib/types'
import { InvestmentForm } from './InvestmentForm'
import { AllocationPanel, holdings } from './Allocation'
import { BankInvestmentsPanel, type BankInvestments } from './BankInvestments'

interface InvestmentsData {
  fixed: Investment[]; crypto: Investment[]; fixed_total: number; crypto_total: number; fx_gain_total: number
  selic: number; selic_date: string | null; selic_is_live: boolean; cdi: number; rate_suggestions: Record<string, number>
}
interface Projection { labels: string[]; invested: number[]; projected: number[]; projected_net: number[] }

/** Valor de uma moeda estrangeira: em reais (cotação de hoje) e, embaixo, o original e o ganho com câmbio. */
function FxNote({ i }: { i: Investment }) {
  const gain = i.fx_gain ?? 0
  return (
    <small className="muted block" title={`Comprado a ${rate(i.fx_buy_rate)} · hoje ${rate(i.fx_rate)}${i.fx_is_live ? '' : ' (cotação indisponível)'}`}>
      {money(i.amount, i.currency)} · câmbio <span className={`money ${gain < 0 ? 'neg' : 'pos'}`}>{gain >= 0 ? '+' : ''}{brl(gain)}</span>
    </small>
  )
}

// No celular sobram só o nome (com pessoa, mês e taxa embaixo), o valor e o botão de remover.
function PortfolioTable({ items, onRemove }: { items: Investment[]; onRemove: (id: number) => void }) {
  return (
    <Table>
      <thead><tr><th>Investimento</th><th className="hide-sm">Pessoa</th><th className="hide-sm">Aplicado em</th><th className="num hide-sm">Taxa a.a.</th>
        <th className="num">Valor</th><th className="num hide-sm">Rende por dia (líq.)</th><th><span className="sr-only">Ações</span></th></tr></thead>
      <tbody>
        {items.map((i) => (
          <tr key={i.id}>
            <td><b>{i.investment_type}</b>{i.description && <small className="muted block">{i.description}</small>}
              <small className="muted block only-sm">{i.user_name} · {MONTHS_SHORT[i.month - 1]}/{i.year} · {pct(i.annual_rate, 2)} a.a.</small></td>
            <td className="hide-sm">{i.user_name}</td>
            <td className="hide-sm">{MONTHS_SHORT[i.month - 1]}/{i.year}</td>
            <td className="num hide-sm">{pct(i.annual_rate, 2)}</td>
            <td className="num"><Money value={i.value_brl ?? i.amount} />{i.currency !== 'BRL' && <FxNote i={i} />}</td>
            <td className="num hide-sm">{i.daily_yield ? brl(i.daily_yield.net) : '—'}{i.daily_yield?.exempt && <small className="muted block">isento</small>}{i.daily_yield?.foreign && <small className="muted block">bruto</small>}</td>
            <td><ConfirmButton label="Remover" confirmLabel="Remover" onConfirm={() => onRemove(i.id)} /></td>
          </tr>))}
      </tbody>
    </Table>
  )
}

function CryptoTable({ items, onRemove }: { items: Investment[]; onRemove: (id: number) => void }) {
  return (
    <Table>
      <thead><tr><th>Moeda</th><th className="hide-sm">Pessoa</th><th className="num hide-sm">Preço de compra</th><th className="num">Valor aplicado</th><th><span className="sr-only">Ações</span></th></tr></thead>
      <tbody>{items.map((i) => (
        <tr key={i.id}>
          <td><b>{i.crypto_coin}</b>{i.description && <small className="muted block">{i.description}</small>}
            <small className="muted block only-sm">{i.user_name}</small></td>
          <td className="hide-sm">{i.user_name}</td>
          <td className="num hide-sm">{i.crypto_buy_price ? brl(i.crypto_buy_price) : '—'}</td>
          <td className="num"><Money value={i.amount} /></td>
          <td><ConfirmButton label="Remover" confirmLabel="Remover" onConfirm={() => onRemove(i.id)} /></td>
        </tr>))}
      </tbody>
    </Table>
  )
}

export function InvestmentsPage() {
  const { dataVersion } = useSession()
  const act = useAction()
  const data = useApi(() => api.get<InvestmentsData>('/investments'), [dataVersion])
  const proj = useApi(() => api.get<Projection>('/charts/investments'), [dataVersion])
  const bank = useApi(() => api.get<BankInvestments>('/pluggy/investments').catch(() => null), [dataVersion])
  const [open, setOpen] = useState(false)
  const d = data.data
  const bankTotal = bank.data?.enabled ? bank.data.total : 0
  const reloadAll = () => { data.reload(); proj.reload() }
  const remove = (id: number) => act(() => api.del<{ message: string }>(`/investments/${id}`), reloadAll)

  return (
    <div className="page">
      <PageHeader title="Investimentos"
        subtitle={d && <>Selic {pct(d.selic, 2)} · CDI {pct(d.cdi, 2)}{d.selic_is_live ? (d.selic_date ? ` (BCB, ${d.selic_date})` : '') : ' (valor de referência — BCB indisponível)'}</>}
        actions={<Button icon="plus" onClick={() => setOpen(true)}>Novo investimento</Button>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : (
        <>
          <dl className="kpis kpis-inline">
            <div><dt>Total investido</dt><dd><Money value={d.fixed_total + d.crypto_total + bankTotal} /></dd></div>
            {bankTotal > 0 && <div><dt>Nos bancos (saldo do banco)</dt><dd><Money value={bankTotal} /></dd></div>}
            {(d.fixed.length > 0 || !bankTotal) && <div><dt>Carteira (em R$)</dt><dd><Money value={d.fixed_total} /></dd></div>}
            {d.fixed.some((i) => i.currency !== 'BRL') && (
              <div><dt>Ganho com câmbio</dt><dd><Money value={d.fx_gain_total} tone="auto" /></dd></div>
            )}
            {d.crypto.length > 0 && <div><dt>Cripto (valor aplicado)</dt><dd><Money value={d.crypto_total} /></dd></div>}
          </dl>
          <AllocationPanel items={holdings(d, bank.data)} />
          {proj.data && proj.data.labels.length > 0 && (
            <Panel title="Projeção da renda fixa — 12 meses">
              <LineChart ariaLabel="Projeção de 12 meses" labels={proj.data.labels} series={[
                { label: 'Aplicado', data: proj.data.invested, color: 'var(--ink-3)' },
                { label: 'Bruto', data: proj.data.projected, color: 'var(--primary)' },
                { label: 'Líquido de IR/IOF', data: proj.data.projected_net, color: 'var(--income)' }]} />
            </Panel>
          )}
          {(d.fixed.length > 0 || !bankTotal) && (
            <Panel title="Carteira" flush>
              {d.fixed.length === 0
                ? <Empty icon="piggy-bank" title="Nenhum investimento na carteira." />
                : <PortfolioTable items={d.fixed} onRemove={remove} />}
            </Panel>
          )}
          {bank.data && <BankInvestmentsPanel data={bank.data} />}
          {d.crypto.length > 0 && <Panel title="Criptomoedas" flush><CryptoTable items={d.crypto} onRemove={remove} /></Panel>}
        </>
      )}
      <InvestmentForm open={open} onClose={() => setOpen(false)} onSaved={reloadAll} suggestions={d?.rate_suggestions ?? {}} />
    </div>
  )
}
