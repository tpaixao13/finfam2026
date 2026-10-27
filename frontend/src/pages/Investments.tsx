import { useState } from 'react'
import { LineChart } from '../components/charts'
import { ConfirmButton, Empty, ErrorBox, Money, PageHeader, Panel, Spinner, Table, Button } from '../components/ui'
import { api } from '../lib/api'
import { brl, dateBR, money, MONTHS_SHORT, pct, rate } from '../lib/format'
import { useApi } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import type { Investment } from '../lib/types'
import { InvestmentForm } from './InvestmentForm'

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

interface BankInvestment {
  id: number; name: string; bank: string; type: string | null; subtype: string | null; issuer: string | null
  balance: number; amount_invested: number | null; amount_withdrawal: number | null; currency: string
  rate: number | null; rate_type: string | null; annual_rate: number | null; due_date: string | null; updated_at: string | null
}
interface BankInvestments { enabled: boolean; items: BankInvestment[]; total: number }

const SUBTYPES: Record<string, string> = {
  STOCK: 'Ação/ETF', ETF: 'ETF', REAL_ESTATE_FUND: 'Fundo imobiliário', BDR: 'BDR', TREASURY: 'Tesouro Direto',
  INVESTMENT_FUND: 'Fundo', MULTIMARKET_FUND: 'Fundo multimercado', FIXED_INCOME_FUND: 'Fundo de renda fixa',
  STOCK_FUND: 'Fundo de ações', PENSION: 'Previdência', SAVINGS: 'Poupança',
}
/** Linha de baixo: tipo traduzido e emissor (sem repetir o que já está no nome). */
function bankDetail(i: BankInvestment): string {
  const kind = i.subtype ? SUBTYPES[i.subtype] ?? i.subtype : null
  const issuer = i.issuer && !i.name.toUpperCase().includes(i.issuer.toUpperCase()) ? i.issuer : null
  return [kind, issuer].filter(Boolean).join(' · ')
}

/** "110% do CDI", "IPCA + 6%", "12,5% a.a." — como o banco descreve a taxa. */
function bankRate(i: BankInvestment): string {
  const kind = (i.rate_type ?? '').toUpperCase()
  if (i.rate != null && ['CDI', 'SELIC'].includes(kind)) return `${pct(i.rate, 2)} do ${kind}`
  if (i.rate != null && kind.startsWith('IPCA')) return `IPCA + ${pct(i.rate, 2)}`
  if (i.annual_rate != null) return `${pct(i.annual_rate, 2)} a.a.`
  if (i.rate != null) return `${pct(i.rate, 2)}${kind ? ` ${kind}` : ''}`
  return '—'
}

/** Investimentos que os bancos conectados informam: só consulta, com o saldo do banco. */
function BankInvestmentsPanel({ data }: { data: BankInvestments }) {
  if (!data.enabled || data.items.length === 0) return null
  return (
    <Panel title={<>Nos bancos <small className="muted">· saldo informado pelo banco (Open Finance), só consulta</small></>} flush
      actions={<b><Money value={data.total} /></b>}>
      <Table>
        <thead><tr><th>Investimento</th><th className="hide-sm">Banco</th><th className="num hide-sm">Taxa</th>
          <th className="hide-sm">Vence</th><th className="num hide-sm">Aplicado</th><th className="num">Saldo hoje</th></tr></thead>
        <tbody>{data.items.map((i) => {
          const gain = i.amount_invested != null ? i.balance - i.amount_invested : null
          return (
            <tr key={i.id}>
              <td><b>{i.name}</b>{bankDetail(i) && <small className="muted block">{bankDetail(i)}</small>}
                <small className="muted block only-sm">{i.bank} · {bankRate(i)}{i.due_date ? ` · vence ${dateBR(i.due_date)}` : ''}</small></td>
              <td className="hide-sm">{i.bank}</td>
              <td className="num hide-sm">{bankRate(i)}</td>
              <td className="hide-sm">{i.due_date ? dateBR(i.due_date) : '—'}</td>
              <td className="num hide-sm">{i.amount_invested != null ? brl(i.amount_invested) : '—'}</td>
              <td className="num">{money(i.balance, i.currency)}
                {gain != null && Math.abs(gain) >= 0.01 && <small className={`block money ${gain < 0 ? 'neg' : 'pos'}`}>{gain >= 0 ? '+' : ''}{brl(gain)}</small>}
                {i.amount_withdrawal != null && <small className="muted block">líquido {brl(i.amount_withdrawal)}</small>}</td>
            </tr>)
        })}</tbody>
      </Table>
    </Panel>
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
            <div><dt>Carteira (em R$)</dt><dd><Money value={d.fixed_total} /></dd></div>
            {d.fixed.some((i) => i.currency !== 'BRL') && (
              <div><dt>Ganho com câmbio</dt><dd><Money value={d.fx_gain_total} tone="auto" /></dd></div>
            )}
            <div><dt>Cripto (valor aplicado)</dt><dd><Money value={d.crypto_total} /></dd></div>
          </dl>
          {proj.data && proj.data.labels.length > 0 && (
            <Panel title="Projeção da renda fixa — 12 meses">
              <LineChart ariaLabel="Projeção de 12 meses" labels={proj.data.labels} series={[
                { label: 'Aplicado', data: proj.data.invested, color: 'var(--ink-3)' },
                { label: 'Bruto', data: proj.data.projected, color: 'var(--primary)' },
                { label: 'Líquido de IR/IOF', data: proj.data.projected_net, color: 'var(--income)' }]} />
            </Panel>
          )}
          <Panel title="Carteira" flush>
            {d.fixed.length === 0
              ? <Empty icon="piggy-bank" title="Nenhum investimento na carteira." />
              : <PortfolioTable items={d.fixed} onRemove={remove} />}
          </Panel>
          {bank.data && <BankInvestmentsPanel data={bank.data} />}
          {d.crypto.length > 0 && <Panel title="Criptomoedas" flush><CryptoTable items={d.crypto} onRemove={remove} /></Panel>}
        </>
      )}
      <InvestmentForm open={open} onClose={() => setOpen(false)} onSaved={reloadAll} suggestions={d?.rate_suggestions ?? {}} />
    </div>
  )
}
