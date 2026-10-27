import { useState } from 'react'
import { Badge, Button, ConfirmButton, IconButton, Input } from '../../components/ui'
import { api } from '../../lib/api'
import { useAction, useSession } from '../../lib/session'
import type { Card } from '../../lib/types'
import { AccountRow } from './AccountRow'
import { openConnect } from './connect'
import { ToggleRow } from './ToggleRow'
import type { Connection } from './types'
import { useBusy } from './useBusy'

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
function BankName({ conn, banks, onChange }: { conn: Connection; banks: string[]; onChange: () => void }) {
  const act = useAction()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(conn.bank_name ?? '')
  const listId = `banks-${conn.id}`
  if (!editing) {
    return (
      <div className="bank-name">
        <h3>{conn.bank_name ?? 'Banco'}</h3>
        {conn.connector_name && conn.connector_name !== conn.bank_name && <span className="muted small">via {conn.connector_name}</span>}
        <IconButton icon="pencil" label={`Renomear ${conn.bank_name ?? 'banco'}`} onClick={() => setEditing(true)} />
      </div>
    )
  }
  async function save(e: React.FormEvent) {
    e.preventDefault()
    await act(() => api.put<{ message: string }>(`/pluggy/items/${conn.id}`, { bank_name: name.trim() }),
      () => { setEditing(false); onChange() })
  }
  return (
    <form className="bank-name" onSubmit={save}>
      <Input value={name} onChange={(e) => setName(e.target.value)} list={listId} maxLength={60} required autoFocus
        aria-label="Nome do banco" />
      <datalist id={listId}>{banks.map((b) => <option key={b} value={b} />)}</datalist>
      <Button type="submit">Salvar</Button>
      <Button type="button" variant="ghost" onClick={() => setEditing(false)}>Cancelar</Button>
    </form>
  )
}

export function ConnectionCard({ conn, cards, banks, onChange }: { conn: Connection; cards: Card[]; banks: string[]; onChange: () => void }) {
  const act = useAction()
  const { toast } = useSession()
  const [busy, run] = useBusy()
  const st = STATUS[conn.status ?? ''] ?? { label: conn.status ?? '—', tone: 'info' as const }
  const bank = conn.bank_name ?? 'banco'
  const reconnect = () => run(async () => {
    try {
      const id = await openConnect(conn.id)
      if (id) await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: id }), onChange)
    } catch (e) { toast((e as Error).message, 'danger') }
  })
  return (
    <article className="bank-conn" style={{ borderLeftColor: conn.bank_color ?? undefined }}>
      <header className="row-between">
        <BankName conn={conn} banks={banks} onChange={onChange} />
        <Badge tone={st.tone}>{st.label}</Badge>
      </header>
      <p className="muted small">Última atualização do banco: {when(conn.last_updated_at)}</p>
      {conn.error && <p className="form-error" role="alert">{conn.error}</p>}
      <ToggleRow label={`Lançar automaticamente o que chega de ${bank}`} title="Lançar automaticamente" checked={conn.auto_import}
        onChange={(v) => act(() => api.put<{ message: string }>(`/pluggy/items/${conn.id}`, { auto_import: v }), onChange)}>
        {conn.auto_import
          ? 'o que chega vira despesa com a categoria sugerida; corrija em Revisar importados › Lançados.'
          : 'desligado: tudo passa por Revisar importados antes.'}
      </ToggleRow>
      {conn.accounts.length ? (
        <ul className="bank-accs">{conn.accounts.map((a) => <AccountRow key={a.id} acc={a} cards={cards} onChange={onChange} />)}</ul>
      ) : <p className="muted">O banco ainda não devolveu contas.</p>}
      <div className="row gap">
        {st.fix
          ? <Button icon="key" busy={busy} onClick={reconnect}>Reconectar</Button>
          : <Button variant="ghost" icon="arrow-repeat" busy={busy}
              onClick={() => run(() => act(() => api.post<{ message: string }>(`/pluggy/items/${conn.id}/refresh`), onChange))}>
              Atualizar agora</Button>}
        <ConfirmButton label={`Desconectar ${bank}`} text="Desconectar" confirmLabel="Confirmar"
          onConfirm={() => act(() => api.del<{ message: string }>(`/pluggy/items/${conn.id}`), onChange)} />
      </div>
    </article>
  )
}
