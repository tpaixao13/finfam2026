// Open Finance (Pluggy) — cada pessoa cadastra a chave da própria conta Pluggy (etapa 1)
// e conecta os bancos pelo widget Pluggy Connect, vendo contas e cartões (etapa 2).
// As peças ficam em ./open-finance/.
import { useState } from 'react'
import { Badge, Button, Empty, ErrorBox, Field, Input, Modal, PageHeader, Panel, Segmented, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Card } from '../lib/types'
import { openConnect } from './open-finance/connect'
import { ConnectionCard } from './open-finance/ConnectionCard'
import { InvoiceCheck } from './open-finance/InvoiceCheck'
import { HowTo, KeyPanel } from './open-finance/KeyPanel'
import type { Connection, PluggyConfig } from './open-finance/types'
import { useBusy } from './open-finance/useBusy'

type Months = '1' | '3' | '6' | '12'
const WINDOWS: { value: Months; label: string }[] = [
  { value: '1', label: '1 mês' }, { value: '3', label: '3 meses' }, { value: '6', label: '6 meses' }, { value: '12', label: '12 meses' },
]

/** Antes de abrir a Pluggy: quanto do passado trazer. Com `withId`, registra uma conexão
 *  já feita no painel da Pluggy (cola o ID dela) em vez de abrir a janela. */
function WindowModal({ open, withId, onClose, onGo }:
  { open: boolean; withId: boolean; onClose: () => void; onGo: (m: number, itemId?: string) => void }) {
  const [months, setMonths] = useState<Months>('1')
  const [itemId, setItemId] = useState('')
  const okId = /^[0-9a-fA-F-]{36}$/.test(itemId.trim())
  return (
    <Modal open={open} title={withId ? 'Usar conexão do painel da Pluggy' : 'Conectar banco'} onClose={onClose} footer={<>
      <Button variant="ghost" onClick={onClose}>Cancelar</Button>
      {withId
        ? <Button icon="check2" disabled={!okId} onClick={() => onGo(Number(months), itemId.trim())}>Usar esta conexão</Button>
        : <Button icon="bank" onClick={() => onGo(Number(months))}>Escolher o banco</Button>}
    </>}>
      <div className="stack">
        {withId && (
          <Field label="ID da conexão (item ID)" hint="No painel da Pluggy, abra a conexão: é o código acima do nome do banco.">
            <Input value={itemId} onChange={(e) => setItemId(e.target.value)} placeholder="add16b55-70fb-4d2d-…"
              autoComplete="off" spellCheck={false} />
          </Field>
        )}
        <div className="field">
          <span className="field-label">Quanto do passado trazer deste banco?</span>
          <Segmented ariaLabel="Período a trazer" value={months} onChange={setMonths} options={WINDOWS} />
        </div>
        <p className="muted small">
          Os lançamentos desse período chegam em <b>Revisar importados</b>. Nada vira despesa sem você aprovar,
          e o que você já lançou à mão ou pelo bot é reconhecido para não duplicar.
        </p>
      </div>
    </Modal>
  )
}

function Connections({ cfg, onChange }: { cfg: PluggyConfig; onChange: () => void }) {
  const items = useApi(() => api.get<{ items: Connection[]; banks: string[] }>('/pluggy/items'), [])
  const cards = useApi(() => api.get<{ cards: Card[] }>('/cards?light=true'), [])
  const act = useAction()
  const { toast } = useSession()
  const [busy, run] = useBusy()
  const [asking, setAsking] = useState<null | 'connect' | 'id'>(null)
  const list = items.data?.items
  const creditCards = (cards.data?.cards ?? []).filter((c) => c.card_type === 'credit' || c.supports_credit)
  const full = !!cfg.max_items && (list?.length ?? 0) >= cfg.max_items
  const reload = () => { items.reload(); onChange() }

  const connect = (months: number, itemId?: string) => {
    setAsking(null)
    return run(async () => {
      try {
        const id = itemId ?? await openConnect()
        if (id) await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: id, months }), reload)
      } catch (e) { toast((e as Error).message, 'danger') }
    })
  }

  return (
    <>
      <Panel title="Bancos conectados" actions={
        <div className="row gap">
          {list && list.length > 0 && (
            <Link to="/bank/review" className="btn btn-ghost">
              Revisar importados{cfg.to_review ? <Badge tone="warning">{cfg.to_review}</Badge> : null}
            </Link>
          )}
          <Button icon="plus" busy={busy} disabled={full} title={full ? 'Limite de bancos da sua família' : undefined}
            onClick={() => setAsking('connect')}>Conectar banco</Button>
        </div>}>
        <WindowModal key={asking ?? 'closed'} open={asking !== null} withId={asking === 'id'}
          onClose={() => setAsking(null)} onGo={connect} />
        {items.error ? <ErrorBox message={items.error} onRetry={items.reload} /> : !list ? <Spinner /> : !list.length ? (
          <Empty icon="bank" title="Nenhum banco conectado">
            Toque em <b>Conectar banco</b>: a janela da Pluggy abre aqui, você escolhe o banco e entra com a senha dele.
            A senha vai direto para a Pluggy, o FinFam nunca vê.
          </Empty>
        ) : (
          <div className="stack">
            {list.map((c) => <ConnectionCard key={c.id} conn={c} cards={creditCards} banks={items.data?.banks ?? []} onChange={reload} />)}
          </div>
        )}
        <p className="muted small">
          {cfg.max_items ? `Limite da sua família: ${cfg.max_items} banco(s) por pessoa. ` : ''}
          Ligue cada cartão do banco ao cartão do FinFam: as compras caem na fatura certa e a fatura pode ser conferida.
          Desconectar apaga os dados vindos do banco; as despesas que você aprovou continuam.
          {!full && <> Já conectou o banco pelo painel da Pluggy?{' '}
            <button type="button" className="link-btn" onClick={() => setAsking('id')}>Usar o ID da conexão</button>.</>}
        </p>
      </Panel>
      {!!list?.length && <InvoiceCheck />}
    </>
  )
}

export function OpenFinancePage() {
  const data = useApi(() => api.get<PluggyConfig>('/pluggy/config'), [])
  const [editing, setEditing] = useState(false)
  const cfg = data.data
  const done = () => { setEditing(false); data.reload() }

  return (
    <div className="page">
      <PageHeader title="Contas conectadas"
        subtitle="Lançamentos e faturas chegando do banco via Open Finance (Pluggy). Em cada banco, você escolhe: revisar antes ou lançar automaticamente." />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !cfg ? <Spinner /> : !cfg.available ? (
        <Panel title="Open Finance não liberado">
          <p className="muted">O administrador ainda não liberou o Open Finance para a sua família.</p>
        </Panel>
      ) : (
        <div className="stack">
          {cfg.configured && !editing && <Connections cfg={cfg} onChange={data.reload} />}
          <div className="grid-2">
            <KeyPanel cfg={cfg} editing={editing} onEdit={() => setEditing(true)} onCancel={() => setEditing(false)} onChanged={done} />
            <HowTo />
          </div>
        </div>
      )}
    </div>
  )
}
