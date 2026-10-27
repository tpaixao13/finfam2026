import { Money, Panel, Table } from '../components/ui'
import { brl, dateBR, money, pct } from '../lib/format'

export interface BankInvestment {
  id: number; name: string; bank: string; type: string | null; subtype: string | null; issuer: string | null
  balance: number; amount_invested: number | null; amount_withdrawal: number | null; currency: string
  rate: number | null; rate_type: string | null; annual_rate: number | null; due_date: string | null; updated_at: string | null
}
export interface BankInvestments { enabled: boolean; items: BankInvestment[]; total: number }

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
export function BankInvestmentsPanel({ data }: { data: BankInvestments }) {
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
              <td className="hide-sm nowrap">{i.bank}</td>
              <td className="num hide-sm nowrap">{bankRate(i)}</td>
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
