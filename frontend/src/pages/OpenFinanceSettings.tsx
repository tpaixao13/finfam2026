// Configurações do Open Finance: a chave Pluggy, avisos e opções avançadas.
// O dia a dia (bancos, faturas) fica em ./OpenFinance.tsx.
import { useState } from 'react'
import { Button, ErrorBox, PageHeader, Panel, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/hooks'
import { Link, navigate } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import { HowTo, KeyPanel } from './open-finance/KeyPanel'
import { ToggleRow } from './open-finance/ToggleRow'
import type { PluggyConfig, Saved } from './open-finance/types'
import { useBusy } from './open-finance/useBusy'
import { WindowModal } from './open-finance/WindowModal'

function Preferences({ cfg, onChanged }: { cfg: PluggyConfig; onChanged: () => void }) {
  const act = useAction()
  const { toast } = useSession()
  const [busy, run] = useBusy()
  const [asking, setAsking] = useState(false)
  const full = !!cfg.max_items && cfg.connections >= cfg.max_items

  const useItem = (months: number, itemId?: string) => {
    setAsking(false)
    if (!itemId) return
    return run(async () => {
      try {
        await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: itemId, months }), () => navigate('/bank'))
      } catch (e) { toast((e as Error).message, 'danger') }
    })
  }

  return (
    <Panel title="Avisos e conexões">
      <div className="stack">
        <ToggleRow label="Avisar no Telegram quando chegar algo do banco" title="Avisar no Telegram" checked={cfg.notify}
          onChange={(v) => act(() => api.put<Saved>('/pluggy/notify', { notify: v }), onChanged)}>
          quando chegar algo do banco (precisa ter o Telegram ligado em <Link to="/telegram">Telegram e IA</Link>).
        </ToggleRow>
        <p className="small">
          <b>Limite de bancos:</b>{' '}
          <span className="muted">
            {cfg.max_items ? `${cfg.connections} de ${cfg.max_items} por pessoa na sua família.` : `${cfg.connections} conectado(s), sem limite.`}
          </span>
        </p>
        <div>
          <h3 className="small">Avançado</h3>
          <p className="muted small">Já conectou o banco pelo painel da Pluggy? Cole o ID da conexão para trazê-la para cá.</p>
          <Button variant="ghost" icon="key" busy={busy} disabled={full}
            title={full ? 'Limite de bancos da sua família' : undefined} onClick={() => setAsking(true)}>
            Usar o ID da conexão</Button>
        </div>
      </div>
      <WindowModal key={asking ? 'open' : 'closed'} open={asking} withId onClose={() => setAsking(false)} onGo={useItem} />
    </Panel>
  )
}

export function OpenFinanceSettingsPage() {
  const data = useApi(() => api.get<PluggyConfig>('/pluggy/config'), [])
  const [editing, setEditing] = useState(false)
  const cfg = data.data
  const done = () => { setEditing(false); data.reload() }

  return (
    <div className="page">
      <PageHeader title="Configurações do Open Finance"
        subtitle="Sua chave Pluggy e as preferências de aviso. Os bancos conectados ficam em Contas conectadas."
        actions={<Link to="/bank" className="btn btn-ghost">← Contas conectadas</Link>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !cfg ? <Spinner /> : !cfg.available ? (
        <Panel title="Open Finance não liberado">
          <p className="muted">O administrador ainda não liberou o Open Finance para a sua família.</p>
        </Panel>
      ) : (
        <div className="stack">
          <div className="grid-2">
            <KeyPanel cfg={cfg} editing={editing} onEdit={() => setEditing(true)} onCancel={() => setEditing(false)} onChanged={done} />
            <HowTo />
          </div>
          {cfg.configured && <Preferences cfg={cfg} onChanged={data.reload} />}
        </div>
      )}
    </div>
  )
}
