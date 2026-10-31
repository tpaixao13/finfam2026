import { Money, Panel } from '../components/ui'
import { api } from '../lib/api'
import { dateBR, money } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useSession } from '../lib/session'

interface BankBalance {
  id: number; bank: string; name: string | null; subtype: string | null; last_digits: string | null
  owner_name?: string | null
  balance: number; currency: string; updated_at: string | null
}
interface BankBalances { enabled: boolean; items: BankBalance[]; total: number; incomes_to_review?: number }

const ACCOUNT_KIND: Record<string, string> = { CHECKING_ACCOUNT: 'Conta corrente', SAVINGS_ACCOUNT: 'Poupança' }

/** Saldo das contas conectadas pelo Open Finance — o que o banco informou na última leitura. */
export function BankBalancesPanel() {
  const { dataVersion } = useSession()
  const data = useApi(() => api.get<BankBalances>('/pluggy/balances').catch(() => null), [dataVersion])
  const b = data.data
  if (!b?.enabled || b.items.length === 0) return null
  return (
    <Panel title={<>Saldo nas contas <small className="muted">· informado pelo banco (Open Finance)</small></>}
      actions={<b><Money value={b.total} tone="auto" /></b>} flush>
      <ul className="balance-list">
        {b.items.map((a) => (
          <li key={a.id}>
            <div>
              <b>{a.bank}</b>{a.owner_name && <span className="muted small"> · de {a.owner_name}</span>}
              <small className="muted block">
                {(a.subtype && ACCOUNT_KIND[a.subtype]) || a.name || 'Conta'}{a.last_digits && ` •••• ${a.last_digits}`}
                {a.updated_at && ` · atualizado em ${dateBR(a.updated_at.slice(0, 10))}`}
              </small>
            </div>
            <span className="num">{money(a.balance, a.currency)}</span>
          </li>))}
      </ul>
      {!!b.incomes_to_review && (
        <p className="small balance-note"><Link to="/bank/incomes">{b.incomes_to_review} entrada(s) do banco para revisar</Link>
          {' '}<span className="muted">— PIX recebido, salário, rendimentos: aprove para virar renda.</span></p>
      )}
    </Panel>
  )
}
