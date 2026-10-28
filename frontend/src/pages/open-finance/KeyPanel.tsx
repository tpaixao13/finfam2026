// Etapa 1 do Open Finance: a chave da conta Pluggy de cada pessoa.
import { useState } from 'react'
import { Badge, Button, Check, ConfirmButton, Field, Input, Panel } from '../../components/ui'
import { api } from '../../lib/api'
import { useAction } from '../../lib/session'
import type { PluggyConfig, Saved } from './types'
import { useBusy } from './useBusy'

function KeyForm({ cfg, onSaved, onCancel }: { cfg: PluggyConfig; onSaved: () => void; onCancel?: () => void }) {
  const act = useAction()
  const [f, setF] = useState({ client_id: '', client_secret: '', consent: false })
  const [busy, run] = useBusy()
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    return run(() => act(() => api.put<Saved>('/pluggy/config', f), onSaved))
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

function KeyStatus({ cfg, onEdit, onChanged }: { cfg: PluggyConfig; onEdit: () => void; onChanged: () => void }) {
  const act = useAction()
  return (
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
        <Button variant="ghost" onClick={onEdit}>Trocar chave</Button>
        <ConfirmButton label="Remover chave Pluggy" text="Remover chave"
          confirmLabel={cfg.connections ? `Remover e desconectar ${cfg.connections} banco(s)` : 'Confirmar remoção'}
          onConfirm={() => act(() => api.del<Saved>('/pluggy/config'), onChanged)} />
      </div>
    </div>
  )
}

export function KeyPanel({ cfg, editing, onEdit, onCancel, onChanged }:
  { cfg: PluggyConfig; editing: boolean; onEdit: () => void; onCancel: () => void; onChanged: () => void }) {
  return (
    <Panel title="Sua chave Pluggy">
      {!cfg.configured || editing
        ? <KeyForm cfg={cfg} onSaved={onChanged} onCancel={editing ? onCancel : undefined} />
        : <KeyStatus cfg={cfg} onEdit={onEdit} onChanged={onChanged} />}
    </Panel>
  )
}

export function HowTo() {
  return (
    <Panel title="Como obter a chave">
      <ol className="plain-list">
        <li>Crie uma conta no painel da Pluggy: <a href="https://dashboard.pluggy.ai" target="_blank" rel="noreferrer">dashboard.pluggy.ai</a>.</li>
        <li>Crie uma <b>aplicação</b> e copie o <b>Client ID</b> e o <b>Client Secret</b>.</li>
        <li>Cole os dois aqui e toque em <b>Salvar e testar</b>.</li>
        <li>Depois, toque em <b>Conectar banco</b> e escolha o seu banco.</li>
      </ol>
      <p className="muted">A chave é <b>sua</b>: cada pessoa da família usa a própria conta Pluggy, e a cobrança (se houver) é feita pela Pluggy na sua conta. O FinFam nunca vê a senha do seu banco.</p>
    </Panel>
  )
}
