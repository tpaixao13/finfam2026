// Contas conectadas (Open Finance/Pluggy): o dia a dia — bancos conectados e conferência de faturas.
// A chave Pluggy e as preferências ficam em ./OpenFinanceSettings.tsx; as peças em ./open-finance/.
import { useState } from 'react'
import { Icon } from '../components/Icon'
import { Badge, Button, Empty, ErrorBox, PageHeader, Panel, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Card } from '../lib/types'
import { openConnect } from './open-finance/connect'
import { ConnectionCard } from './open-finance/ConnectionCard'
import type { Connection, PluggyConfig } from './open-finance/types'
import { useBusy } from './open-finance/useBusy'
import { WindowModal } from './open-finance/WindowModal'

/** Ações do topo: revisar importados, conectar banco e ir às configurações. */
function HeaderActions({ cfg, onConnected }: { cfg: PluggyConfig; onConnected: () => void }) {
  const act = useAction()
  const { toast } = useSession()
  const [busy, run] = useBusy()
  const [asking, setAsking] = useState(false)
  const full = !!cfg.max_items && cfg.connections >= cfg.max_items

  const connect = (months: number) => {
    setAsking(false)
    return run(async () => {
      try {
        const id = await openConnect()
        if (id) await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: id, months }), onConnected)
      } catch (e) { toast((e as Error).message, 'danger') }
    })
  }

  return (
    <div className="row gap">
      <Link to="/bank/settings" className="btn btn-ghost"><Icon name="gear" /><span>Configurações</span></Link>
      {(cfg.configured || cfg.to_review > 0) && <Link to="/bank/review" className="btn btn-ghost">
        Revisar importados{cfg.to_review ? <Badge tone="warning">{cfg.to_review}</Badge> : null}
      </Link>}
      {cfg.configured && <>
        <Button icon="plus" busy={busy} disabled={full} title={full ? 'Limite de bancos da sua família' : undefined}
          onClick={() => setAsking(true)}>Conectar banco</Button>
        <WindowModal key={asking ? 'open' : 'closed'} open={asking} withId={false} onClose={() => setAsking(false)} onGo={connect} />
      </>}
    </div>
  )
}

function Connections({ version, onChange }: { version: number; onChange: () => void }) {
  const items = useApi(() => api.get<{ items: Connection[]; banks: string[] }>('/pluggy/items'), [version])
  const cards = useApi(() => api.get<{ cards: Card[] }>('/cards?light=true'), [])
  const list = items.data?.items
  const creditCards = (cards.data?.cards ?? []).filter((c) => c.card_type === 'credit' || c.supports_credit)
  const reload = () => { items.reload(); onChange() }

  return (
    <>
      <Panel title="Bancos conectados">
        {items.error ? <ErrorBox message={items.error} onRetry={items.reload} /> : !list ? <Spinner /> : !list.length ? (
          <Empty icon="bank" title="Nenhum banco conectado">
            Toque em <b>Conectar banco</b>: a janela da Pluggy abre aqui e você entra com a senha do banco.
            A senha vai direto para a Pluggy; o FinFam nunca vê.
          </Empty>
        ) : (
          <div className="stack">
            {list.map((c) => <ConnectionCard key={c.id} conn={c} cards={creditCards} banks={items.data?.banks ?? []} onChange={reload} />)}
          </div>
        )}
      </Panel>
      {!!list?.length && <p className="muted small">A conferência das faturas com o banco fica em <Link to="/cards">Cartões</Link>, no mês de cada fatura.</p>}
    </>
  )
}

export function OpenFinancePage() {
  const data = useApi(() => api.get<PluggyConfig>('/pluggy/config'), [])
  const [version, setVersion] = useState(0)
  const cfg = data.data
  const refresh = () => { setVersion((v) => v + 1); data.reload() }

  return (
    <div className="page">
      <PageHeader title="Contas conectadas"
        subtitle="Lançamentos e faturas chegando do banco. Em cada banco você escolhe: revisar antes ou lançar automaticamente."
        actions={cfg?.available ? <HeaderActions cfg={cfg} onConnected={refresh} /> : undefined} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !cfg ? <Spinner /> : !cfg.available ? (
        <Panel title="Open Finance não liberado">
          <p className="muted">O administrador ainda não liberou o Open Finance para a sua família.</p>
        </Panel>
      ) : !cfg.configured ? (
        <div className="stack">
          <Panel title="Conecte os seus bancos">
            <div className="stack">
              <p>{cfg.family_key
                ? <>A família já tem uma chave Pluggy (de <b>{cfg.family_key.owner_name}</b>): é só usar a mesma para conectar os seus bancos.</>
                : 'Para conectar seus bancos, primeiro cadastre a chave Pluggy (uma por família basta).'}</p>
              <div><Link to="/bank/settings" className="btn btn-primary">
                {cfg.family_key ? 'Usar a chave da família' : 'Cadastrar chave Pluggy'}</Link></div>
            </div>
          </Panel>
          <Connections version={version} onChange={data.reload} />
        </div>
      ) : (
        <div className="stack"><Connections version={version} onChange={data.reload} /></div>
      )}
    </div>
  )
}
