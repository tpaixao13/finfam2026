// Tokens de API: o que liga o FinFam a um assistente de IA pelo MCP (Claude, etc.).
import { useState } from 'react'
import { api } from '../lib/api'
import { dateBR } from '../lib/format'
import { useApi } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import { Badge, Button, ConfirmButton, Empty, Field, Input, Panel, Table } from './ui'

interface ApiToken { id: number; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked: boolean }

export function ApiTokensPanel() {
  const { toast } = useSession()
  const act = useAction()
  const tokens = useApi(() => api.get<ApiToken[]>('/auth/tokens'), [])
  const [name, setName] = useState('Claude (MCP)')
  const [created, setCreated] = useState<string | null>(null)

  async function create(e: React.FormEvent) {
    e.preventDefault()
    try {
      setCreated((await api.post<{ token: string }>('/auth/tokens', { name })).token)
      tokens.reload()
    } catch (err) {
      toast((err as Error).message, 'danger')
    }
  }

  return (
    <Panel title="Assistente de IA no computador (MCP)">
      <p className="muted">
        Para usar o FinFam dentro do Claude ou de outro assistente compatível com MCP: gere um token, cole na
        configuração do servidor MCP do FinFam e pergunte coisas como "quanto gastei com mercado este mês?".
        O token age em seu nome; use um por aplicativo e revogue o que não usar mais. O bot do Telegram não precisa de token.
      </p>
      <form className="inline-form" onSubmit={create}>
        <Field label="Nome do token"><Input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={60} /></Field>
        <Button type="submit" icon="key">Gerar token</Button>
      </form>
      {created && (
        <div className="token-reveal" role="status">
          <p><b>Copie agora</b> — este token não será mostrado de novo.</p>
          <code>{created}</code>
          <Button variant="ghost" onClick={() => { navigator.clipboard?.writeText(created); toast('Token copiado.') }}>Copiar</Button>
        </div>
      )}
      {tokens.data && tokens.data.length > 0 ? (
        <Table>
          <thead><tr><th>Nome</th><th>Início</th><th className="hide-sm">Criado</th><th>Último uso</th><th><span className="sr-only">Ações</span></th></tr></thead>
          <tbody>{tokens.data.map((t) => (
            <tr key={t.id} className={t.revoked ? 'row-off' : ''}>
              <td>{t.name}</td><td><code>{t.prefix}…</code></td><td className="hide-sm">{dateBR(t.created_at)}</td>
              <td>{t.last_used_at ? dateBR(t.last_used_at) : 'nunca'}</td>
              <td>{t.revoked ? <Badge>Revogado</Badge> : <ConfirmButton label={`Revogar ${t.name}`} confirmLabel="Revogar"
                onConfirm={() => act(() => api.del<{ message: string }>(`/auth/tokens/${t.id}`), tokens.reload)} />}</td>
            </tr>))}
          </tbody>
        </Table>
      ) : tokens.data && <Empty icon="key" title="Nenhum token criado." />}
    </Panel>
  )
}
