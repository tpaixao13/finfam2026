import { useState } from 'react'
import { Badge, Button, ConfirmButton, Input } from '../../components/ui'
import { api } from '../../lib/api'
import { useAction, useSession } from '../../lib/session'
import type { Card } from '../../lib/types'
import { AccountRow, AccountSettings } from './AccountRow'
import { openConnect } from './connect'
import { ToggleRow } from './ToggleRow'
import type { Connection } from './types'
import { SaveField } from './SaveField'
import { useBusy, useBusyAction } from './useBusy'

const STATUS: Record<string, { label: string; tone: 'success' | 'info' | 'warning' | 'danger'; fix?: boolean }> = {
  UPDATED: { label: 'Atualizado', tone: 'success' },
  UPDATING: { label: 'Atualizando…', tone: 'info' },
  OUTDATED: { label: 'Desatualizado', tone: 'warning' },
  LOGIN_ERROR: { label: 'Senha do banco recusada', tone: 'danger', fix: true },
  WAITING_USER_INPUT: { label: 'Precisa de você', tone: 'warning', fix: true },
  WAITING_USER_ACTION: { label: 'Precisa de você', tone: 'warning', fix: true },
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : 'nunca'
}

/** Nome do banco (o Meu Pluggy vem como "MeuPluggy"): vai nas despesas lançadas. */
function RenameForm({ conn, banks, onChange }: { conn: Connection; banks: string[]; onChange: () => void }) {
  const [busy, act] = useBusyAction()
  const saved = conn.bank_name ?? ''
  const [name, setName] = useState(saved)
  const listId = `banks-${conn.id}`
  const clean = name.trim()
  return (
    <SaveField label="Nome do banco nas despesas" hint="Aparece como banco em cada despesa que vier daqui."
      dirty={!!clean && clean !== saved} busy={busy}
      onSave={() => act(() => api.put<{ message: string }>(`/pluggy/items/${conn.id}`, { bank_name: clean }), onChange)}>
      <Input value={name} onChange={(e) => setName(e.target.value)} list={listId} maxLength={60} required className="input no-list-arrow" />
      <datalist id={listId}>{banks.map((b) => <option key={b} value={b} />)}</datalist>
    </SaveField>
  )
}

export function ConnectionCard({ conn, cards, banks, onChange }: { conn: Connection; cards: Card[]; banks: string[]; onChange: () => void }) {
  const act = useAction()
  const { toast } = useSession()
  const [busy, run] = useBusy()
  const [refreshing, refresh] = useBusyAction()
  const [open, setOpen] = useState(false)
  const st = STATUS[conn.status ?? ''] ?? { label: conn.status ?? '—', tone: 'info' as const }
  const bank = conn.bank_name ?? 'banco'
  const panelId = `bank-settings-${conn.id}`
  const creditAccounts = conn.accounts.filter((a) => a.type === 'CREDIT')
  const reconnect = () => run(async () => {
    try {
      const id = await openConnect(conn.id)
      if (id) await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: id }), onChange)
    } catch (e) { toast((e as Error).message, 'danger') }
  })
  return (
    <article className="bank-conn" style={{ borderLeftColor: conn.bank_color ?? undefined }}>
      <header className="row-between">
        <div className="bank-name">
          <h3>{bank === 'banco' ? 'Banco' : bank}</h3>
          {conn.connector_name && conn.connector_name !== conn.bank_name && <span className="muted small">via {conn.connector_name}</span>}
        </div>
        <span className="row gap">
          {conn.shared && <Badge tone="info">{conn.mine ? 'Compartilhado com a família' : `De ${conn.owner_name ?? 'outra pessoa'}`}</Badge>}
          <Badge tone={st.tone}>{st.label}</Badge>
        </span>
      </header>
      <p className="muted small">Última atualização do banco: {when(conn.last_updated_at)}</p>
      {conn.error && <p className="form-error" role="alert">{conn.error}</p>}
      {conn.accounts.length
        ? <ul className="bank-accs">{conn.accounts.map((a) => <AccountRow key={a.id} acc={a} mine={conn.mine} />)}</ul>
        : <p className="muted">O banco ainda não devolveu contas.</p>}
      {!conn.mine ? (
        <p className="muted small">{conn.owner_name ?? 'Outra pessoa'} compartilhou este banco: você vê e revisa o que chega
          dele em Revisar importados. Conectar, atualizar e ajustar ficam com {conn.owner_name ?? 'quem conectou'}.</p>
      ) : <div className="row gap">
        {st.fix
          ? <Button icon="key" busy={busy} onClick={reconnect}>Reconectar</Button>
          : <Button variant="ghost" icon="arrow-repeat" busy={refreshing}
              onClick={() => refresh(() => api.post<{ message: string }>(`/pluggy/items/${conn.id}/refresh`), onChange)}>
              Atualizar agora</Button>}
        <Button variant="ghost" icon="gear" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>
          Ajustes</Button>
      </div>}
      {conn.mine && open && (
        <div id={panelId} className="bank-settings">
          <RenameForm conn={conn} banks={banks} onChange={onChange} />
          <ToggleRow label={`Compartilhar ${bank} com a família`} title="Compartilhar com a família" checked={conn.shared}
            onChange={(v) => act(() => api.put<{ message: string }>(`/pluggy/items/${conn.id}`, { shared: v }), onChange)}>
            {conn.shared
              ? 'a família vê saldo, cartões e o que chega deste banco, e pode lançar ou ignorar na revisão.'
              : 'desligado: só você vê este banco.'}
          </ToggleRow>
          <ToggleRow label={`Lançar automaticamente o que chega de ${bank}`} title="Lançar automaticamente" checked={conn.auto_import}
            onChange={(v) => act(() => api.put<{ message: string }>(`/pluggy/items/${conn.id}`, { auto_import: v }), onChange)}>
            {conn.auto_import
              ? 'o que chega vira despesa com a categoria sugerida; corrija em Revisar importados › Lançados.'
              : 'desligado: tudo passa por Revisar importados antes.'}
          </ToggleRow>
          {creditAccounts.length > 0 && (
            <ul className="bank-accs">{creditAccounts.map((a) => <AccountSettings key={a.id} acc={a} cards={cards} onChange={onChange} />)}</ul>
          )}
          <div>
            <ConfirmButton label={`Desconectar ${bank}`} text="Desconectar" confirmLabel="Confirmar"
              onConfirm={() => act(() => api.del<{ message: string }>(`/pluggy/items/${conn.id}`), onChange)} />
            <p className="muted small">Desconectar apaga os dados vindos do banco; as despesas que você aprovou continuam.</p>
          </div>
        </div>
      )}
    </article>
  )
}
