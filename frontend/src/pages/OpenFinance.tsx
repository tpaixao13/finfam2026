// Open Finance (Pluggy) — etapa 1: cada pessoa cadastra a chave da própria conta Pluggy.
import { useState } from 'react'
import { Badge, Button, Check, ConfirmButton, ErrorBox, Field, Input, PageHeader, Panel, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/session'

interface PluggyConfig {
  available: boolean; max_items: number | null; configured: boolean; client_id_masked: string
  status: 'ok' | 'error' | null; last_error: string | null; webhook: boolean; public_url_ok: boolean
}
type Saved = { message: string; config: PluggyConfig }

function KeyForm({ cfg, onSaved, onCancel }: { cfg: PluggyConfig; onSaved: () => void; onCancel?: () => void }) {
  const act = useAction()
  const [f, setF] = useState({ client_id: '', client_secret: '', consent: false })
  const [busy, setBusy] = useState(false)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    await act(() => api.put<Saved>('/pluggy/config', f), onSaved)
    setBusy(false)
  }
  return (
    <form className="stack" onSubmit={submit}>
      <Field label="Client ID" hint={cfg.configured ? `Atual: ${cfg.client_id_masked}` : 'Formato: 9f3c1a2e-7b44-4d0e-…'}>
        <Input value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value.trim() })} required
          autoComplete="off" spellCheck={false} />
      </Field>
      <Field label="Client Secret" hint="Fica guardado criptografado e nunca volta para a tela.">
        <Input type="password" value={f.client_secret} onChange={(e) => setF({ ...f, client_secret: e.target.value })}
          required autoComplete="off" />
      </Field>
      <Check checked={f.consent} onChange={(v) => setF({ ...f, consent: v })}
        label="Concordo que o FinFam guarde os extratos e faturas importados dos bancos que eu conectar, e que apague tudo ao desconectar." />
      <div className="row gap">
        <Button type="submit" busy={busy} disabled={!f.consent}>Salvar e testar</Button>
        {onCancel && <Button type="button" variant="ghost" onClick={onCancel}>Cancelar</Button>}
      </div>
    </form>
  )
}

function HowTo() {
  return (
    <Panel title="Como obter a chave">
      <ol className="plain-list">
        <li>Crie uma conta no painel da Pluggy: <a href="https://dashboard.pluggy.ai" target="_blank" rel="noreferrer">dashboard.pluggy.ai</a>.</li>
        <li>Crie uma <b>aplicação</b> e copie o <b>Client ID</b> e o <b>Client Secret</b>.</li>
        <li>Cole os dois aqui e toque em <b>Salvar e testar</b>.</li>
      </ol>
      <p className="muted">A chave é <b>sua</b>: cada pessoa da família usa a própria conta Pluggy, e a cobrança (se houver) é feita pela Pluggy na sua conta. O FinFam nunca vê a senha do seu banco.</p>
    </Panel>
  )
}

export function OpenFinancePage() {
  const data = useApi(() => api.get<PluggyConfig>('/pluggy/config'), [])
  const act = useAction()
  const [editing, setEditing] = useState(false)
  const cfg = data.data
  const done = () => { setEditing(false); data.reload() }

  return (
    <div className="page">
      <PageHeader title="Contas conectadas"
        subtitle="Lançamentos e faturas chegando do banco via Open Finance (Pluggy). Nada é lançado sem você revisar." />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !cfg ? <Spinner /> : !cfg.available ? (
        <Panel title="Open Finance não liberado">
          <p className="muted">O administrador ainda não liberou o Open Finance para a sua família.</p>
        </Panel>
      ) : (
        <div className="grid-2">
          <Panel title="Sua chave Pluggy">
            {!cfg.configured || editing ? <KeyForm cfg={cfg} onSaved={done} onCancel={editing ? () => setEditing(false) : undefined} /> : (
              <div className="stack">
                <p>
                  {cfg.status === 'ok' ? <Badge tone="success">Chave aceita</Badge> : <Badge tone="danger">Com problema</Badge>}
                  {' '}<span className="muted">Client ID {cfg.client_id_masked}</span>
                </p>
                {cfg.last_error && <p className="form-error" role="alert">{cfg.last_error}</p>}
                <p className="muted">
                  {cfg.webhook ? 'Aviso de novidades (webhook) configurado na sua conta Pluggy.'
                    : cfg.public_url_ok ? 'Webhook ainda não configurado: salve a chave de novo.'
                      : 'Webhook pendente: o administrador precisa definir PUBLIC_URL (https) no servidor.'}
                </p>
                <div className="row gap">
                  <Button variant="ghost" onClick={() => setEditing(true)}>Trocar chave</Button>
                  <ConfirmButton label="Remover chave Pluggy" text="Remover chave" confirmLabel="Confirmar remoção"
                    onConfirm={() => act(() => api.del<Saved>('/pluggy/config'), done)} />
                </div>
              </div>
            )}
          </Panel>
          {cfg.configured && !editing ? (
            <Panel title="Bancos conectados">
              <p className="muted">Conectar bancos chega na próxima etapa. Com a chave salva, você já está pronto.</p>
              {cfg.max_items && <p className="muted">Limite da sua família: {cfg.max_items} banco(s) por pessoa.</p>}
              <div><Button icon="plus" disabled title="Próxima etapa">Conectar banco</Button></div>
            </Panel>
          ) : <HowTo />}
        </div>
      )}
    </div>
  )
}
