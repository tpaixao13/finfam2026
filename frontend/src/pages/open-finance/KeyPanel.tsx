// Etapa 1 do Open Finance: a chave Pluggy. Uma por família basta: quem cadastra primeiro, os outros usam a mesma.
import { useState } from 'react'
import { Badge, Button, Check, ConfirmButton, Field, Input, Panel } from '../../components/ui'
import { api } from '../../lib/api'
import { useAction } from '../../lib/session'
import type { PluggyConfig, Saved } from './types'
import { useBusy } from './useBusy'

const CONSENT = 'Concordo que o FinFam guarde os extratos e faturas importados dos bancos que eu conectar, e que apague tudo ao desconectar.'

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
      <Check checked={f.consent} onChange={(v) => setF({ ...f, consent: v })} label={CONSENT} />
      <div className="row gap">
        <Button type="submit" busy={busy} disabled={!f.consent}>Salvar e testar</Button>
        {onCancel && <Button type="button" variant="ghost" onClick={onCancel}>Cancelar</Button>}
      </div>
    </form>
  )
}

function FamilyKeyOffer({ cfg, onSaved, onOwnKey }: { cfg: PluggyConfig; onSaved: () => void; onOwnKey: () => void }) {
  const act = useAction()
  const [consent, setConsent] = useState(false)
  const [busy, run] = useBusy()
  const fam = cfg.family_key!
  return (
    <div className="stack">
      <p>A família já tem uma chave Pluggy, cadastrada por <b>{fam.owner_name ?? 'outra pessoa'}</b>{' '}
        <span className="muted">(Client ID {fam.client_id_masked})</span>. Use a mesma: não precisa criar conta na Pluggy.</p>
      <p className="muted small">Cada pessoa continua vendo só os bancos que ela mesma conectar.</p>
      <Check checked={consent} onChange={setConsent} label={CONSENT} />
      <div className="row gap">
        <Button busy={busy} disabled={!consent}
          onClick={() => run(() => act(() => api.post<Saved>('/pluggy/config/family', { consent }), onSaved))}>
          Usar a chave da família</Button>
        <Button variant="ghost" onClick={onOwnKey}>Cadastrar uma chave própria</Button>
      </div>
    </div>
  )
}

/** Só quem cadastrou a chave decide se a família pode usar. Tirar desconecta os bancos que a família ligou com ela. */
function FamilySharing({ cfg, sharedWith, onChanged }: { cfg: PluggyConfig; sharedWith: string[]; onChanged: () => void }) {
  const act = useAction()
  const set = (allow: boolean) => act(() => api.put<Saved>('/pluggy/config/family-sharing', { allow }), onChanged)
  if (!cfg.allow_family) {
    return (
      <div className="stack-sm">
        <p className="muted small">Só você usa esta chave. Se deixar, as pessoas da família conectam os bancos delas sem criar
          conta na Pluggy — mas esses bancos ficam na <b>sua</b> conta Pluggy (você os vê no painel da Pluggy).</p>
        <div><Button variant="ghost" onClick={() => set(true)}>Deixar a família usar esta chave</Button></div>
      </div>
    )
  }
  return (
    <div className="stack-sm">
      <p className="muted">A família pode usar esta chave{sharedWith.length ? <>: {sharedWith.join(', ')} já usa(m)</> : ''}.
        Se você trocar o segredo, muda para todos.</p>
      <div><ConfirmButton label="Parar de compartilhar a chave" text="Parar de compartilhar"
        confirmLabel={sharedWith.length ? `Parar e desconectar ${cfg.family_banks} banco(s) da família` : 'Confirmar'}
        onConfirm={() => set(false)} /></div>
    </div>
  )
}

function KeyStatus({ cfg, onEdit, onChanged }: { cfg: PluggyConfig; onEdit: () => void; onChanged: () => void }) {
  const act = useAction()
  const sharedWith = cfg.shared_with.filter((n): n is string => !!n)
  return (
    <div className="stack">
      <p>
        {cfg.status === 'ok' ? <Badge tone="success">Chave aceita</Badge> : <Badge tone="danger">Com problema</Badge>}
        {' '}<span className="muted">Client ID {cfg.client_id_masked}</span>
      </p>
      {cfg.shared_from_name && <p>Você usa a chave da família, cadastrada por <b>{cfg.shared_from_name}</b>.</p>}
      {!cfg.shared_from_name && <FamilySharing cfg={cfg} sharedWith={sharedWith} onChanged={onChanged} />}
      {cfg.last_error && <p className="form-error" role="alert">{cfg.last_error}</p>}
      <p className="muted">
        {cfg.webhook ? 'Aviso de novidades (webhook) configurado na sua conta Pluggy.'
          : cfg.public_url_ok ? 'Webhook ainda não configurado: salve a chave de novo.'
            : 'Webhook pendente: o administrador precisa definir PUBLIC_URL (https) no servidor.'}
      </p>
      <div className="row gap">
        <Button variant="ghost" onClick={onEdit}>{cfg.shared_from_name ? 'Usar uma chave própria' : 'Trocar chave'}</Button>
        <ConfirmButton label="Remover chave Pluggy" text={cfg.shared_from_name ? 'Parar de usar' : 'Remover chave'}
          confirmLabel={cfg.connections + cfg.family_banks
            ? `Remover e desconectar ${cfg.connections + cfg.family_banks} banco(s)${cfg.family_banks ? ' (inclui os da família)' : ''}`
            : 'Confirmar remoção'}
          onConfirm={() => act(() => api.del<Saved>('/pluggy/config'), onChanged)} />
      </div>
    </div>
  )
}

export function KeyPanel({ cfg, editing, onEdit, onCancel, onChanged }:
  { cfg: PluggyConfig; editing: boolean; onEdit: () => void; onCancel: () => void; onChanged: () => void }) {
  const offer = !cfg.configured && !editing && cfg.family_key
  const form = !cfg.configured || editing
  return (
    <Panel title="Chave Pluggy">
      {offer ? <FamilyKeyOffer cfg={cfg} onSaved={onChanged} onOwnKey={onEdit} />
        : form ? <KeyForm cfg={cfg} onSaved={onChanged} onCancel={editing ? onCancel : undefined} />
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
      <p className="muted"><b>Uma chave por família basta:</b> quem cadastrar deixa a família usar, e os outros tocam em
        <b> Usar a chave da família</b>. Cada pessoa continua vendo só os bancos que ela conectar. A cobrança (se houver)
        é feita pela Pluggy na conta de quem criou a aplicação. O FinFam nunca vê a senha do seu banco.</p>
    </Panel>
  )
}
