// Open Finance (Pluggy) — cada pessoa cadastra a chave da própria conta Pluggy (etapa 1)
// e conecta os bancos pelo widget Pluggy Connect, vendo contas e cartões (etapa 2).
import { useState } from 'react'
import { Badge, Button, Check, ConfirmButton, Empty, ErrorBox, Field, IconButton, Input, Modal, PageHeader, Panel, Segmented, Select, Spinner, Switch } from '../components/ui'
import { api } from '../lib/api'
import { money } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Card } from '../lib/types'

interface PluggyConfig {
  available: boolean; max_items: number | null; configured: boolean; client_id_masked: string
  status: 'ok' | 'error' | null; last_error: string | null; webhook: boolean; public_url_ok: boolean
  include_sandbox: boolean; connections: number; to_review: number
}
type Saved = { message: string; config: PluggyConfig }

interface BankAccount {
  id: number; type: 'BANK' | 'CREDIT'; subtype: string | null; name: string | null; last_digits: string | null
  currency: string; balance: number | null; credit_limit: number | null; card_id: number | null; card_label: string | null
}
interface Connection {
  id: number; item_id: string; bank_name: string | null; connector_name: string | null; auto_import: boolean; bank_color: string | null; status: string | null
  execution_status: string | null; error: string | null; last_updated_at: string | null; accounts: BankAccount[]
}

// ── Widget Pluggy Connect (carregado só quando a pessoa toca em "Conectar banco") ──
const CONNECT_JS = 'https://cdn.pluggy.ai/pluggy-connect/v2.7.0/pluggy-connect.js'
type ConnectOpts = {
  connectToken: string; includeSandbox?: boolean; updateItem?: string
  onSuccess: (d: { item: { id: string } }) => void; onError?: (e: { message?: string }) => void; onClose?: () => void
}
declare global { interface Window { PluggyConnect?: new (o: ConnectOpts) => { init: () => void } } }
let loading: Promise<void> | null = null

function loadConnect(): Promise<void> {
  if (window.PluggyConnect) return Promise.resolve()
  loading ??= new Promise((ok, fail) => {
    const s = document.createElement('script')
    s.src = CONNECT_JS
    s.async = true
    s.onload = () => ok()
    s.onerror = () => { loading = null; fail(new Error('Não consegui abrir o Pluggy Connect. Confira a internet e tente de novo.')) }
    document.head.appendChild(s)
  })
  return loading
}

/** Abre o Pluggy Connect; resolve com o id do item conectado (ou null se a pessoa fechou). */
async function openConnect(connectionId?: number): Promise<string | null> {
  const [tok] = await Promise.all([
    api.post<{ connect_token: string; item_id: string | null; include_sandbox: boolean }>(
      '/pluggy/connect-token', { connection_id: connectionId ?? null }),
    loadConnect(),
  ])
  return new Promise((resolve, reject) => {
    new window.PluggyConnect!({
      connectToken: tok.connect_token,
      includeSandbox: tok.include_sandbox,
      ...(tok.item_id ? { updateItem: tok.item_id } : {}),
      onSuccess: ({ item }) => resolve(item.id),
      onError: (e) => reject(new Error(e?.message || 'O banco não conectou. Tente de novo.')),
      onClose: () => resolve(null),
    }).init()
  })
}

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

function AccountRow({ acc, cards, onChange }: { acc: BankAccount; cards: Card[]; onChange: () => void }) {
  const act = useAction()
  const credit = acc.type === 'CREDIT'
  const label = acc.name || (credit ? 'Cartão de crédito' : 'Conta')
  return (
    <li className="bank-acc">
      <div className="bank-acc-main">
        <b>{label}</b>{acc.last_digits && <span className="muted"> •••• {acc.last_digits}</span>}
        <div className="muted small">
          {credit ? `Fatura atual ${money(acc.balance == null ? null : Math.abs(acc.balance), acc.currency)}${acc.credit_limit ? ` · limite ${money(acc.credit_limit, acc.currency)}` : ''}`
            : `Saldo ${money(acc.balance, acc.currency)}`}
        </div>
      </div>
      {credit && (
        <div className="bank-acc-map"><Select aria-label={`Cartão do FinFam para ${label}`} value={acc.card_id ?? ''}
          placeholder="Ligar a um cartão…"
          options={cards.map((c) => ({ value: c.id, label: c.label }))}
          onChange={(e) => act(() => api.put<{ message: string }>(`/pluggy/accounts/${acc.id}`,
            { card_id: e.target.value ? Number(e.target.value) : null }), onChange)} /></div>
      )}
    </li>
  )
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

function ConnectionCard({ conn, cards, banks, onChange }: { conn: Connection; cards: Card[]; banks: string[]; onChange: () => void }) {
  const act = useAction()
  const { toast } = useSession()
  const [busy, setBusy] = useState(false)
  const st = STATUS[conn.status ?? ''] ?? { label: conn.status ?? '—', tone: 'info' as const }
  async function reconnect() {
    setBusy(true)
    try {
      const id = await openConnect(conn.id)
      if (id) await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: id }), onChange)
    } catch (e) { toast((e as Error).message, 'danger') }
    setBusy(false)
  }
  return (
    <article className="bank-conn" style={{ borderLeftColor: conn.bank_color ?? undefined }}>
      <header className="row-between">
        <BankName conn={conn} banks={banks} onChange={onChange} />
        <Badge tone={st.tone}>{st.label}</Badge>
      </header>
      <p className="muted small">Última atualização do banco: {when(conn.last_updated_at)}</p>
      {conn.error && <p className="form-error" role="alert">{conn.error}</p>}
      <div className="bank-auto">
        <Switch label={`Lançar automaticamente o que chega de ${conn.bank_name ?? 'banco'}`} checked={conn.auto_import}
          onChange={(v) => act(() => api.put<{ message: string }>(`/pluggy/items/${conn.id}`, { auto_import: v }), onChange)} />
        <span className="small">
          <b>Lançar automaticamente</b>
          <span className="muted"> — {conn.auto_import
            ? 'o que chega vira despesa com a categoria sugerida; corrija em Revisar importados › Lançados.'
            : 'desligado: tudo passa por Revisar importados antes.'}</span>
        </span>
      </div>
      {conn.accounts.length ? (
        <ul className="bank-accs">{conn.accounts.map((a) => <AccountRow key={a.id} acc={a} cards={cards} onChange={onChange} />)}</ul>
      ) : <p className="muted">O banco ainda não devolveu contas.</p>}
      <div className="row gap">
        {st.fix
          ? <Button icon="key" busy={busy} onClick={reconnect}>Reconectar</Button>
          : <Button variant="ghost" icon="arrow-repeat" busy={busy}
              onClick={async () => { setBusy(true); await act(() => api.post<{ message: string }>(`/pluggy/items/${conn.id}/refresh`), onChange); setBusy(false) }}>
              Atualizar agora</Button>}
        <ConfirmButton label={`Desconectar ${conn.bank_name ?? 'banco'}`} text="Desconectar" confirmLabel="Confirmar"
          onConfirm={() => act(() => api.del<{ message: string }>(`/pluggy/items/${conn.id}`), onChange)} />
      </div>
    </article>
  )
}

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
        <p>Quanto do passado trazer deste banco?</p>
        <Segmented ariaLabel="Período a trazer" value={months} onChange={setMonths} options={WINDOWS} />
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
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState<null | 'connect' | 'id'>(null)
  const list = items.data?.items
  const creditCards = (cards.data?.cards ?? []).filter((c) => c.card_type === 'credit' || c.supports_credit)
  const full = !!cfg.max_items && (list?.length ?? 0) >= cfg.max_items
  const reload = () => { items.reload(); onChange() }

  async function connect(months: number, itemId?: string) {
    setAsking(null)
    setBusy(true)
    try {
      const id = itemId ?? await openConnect()
      if (id) await act(() => api.post<{ message: string }>('/pluggy/items', { item_id: id, months }), reload)
    } catch (e) { toast((e as Error).message, 'danger') }
    setBusy(false)
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
    </>
  )
}

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
        <li>Depois, toque em <b>Conectar banco</b> e escolha o seu banco.</li>
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
        subtitle="Lançamentos e faturas chegando do banco via Open Finance (Pluggy). Em cada banco, você escolhe: revisar antes ou lançar automaticamente." />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !cfg ? <Spinner /> : !cfg.available ? (
        <Panel title="Open Finance não liberado">
          <p className="muted">O administrador ainda não liberou o Open Finance para a sua família.</p>
        </Panel>
      ) : (
        <div className="stack">
          {cfg.configured && !editing && <Connections cfg={cfg} onChange={data.reload} />}
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
                  <ConfirmButton label="Remover chave Pluggy" text="Remover chave"
                    confirmLabel={cfg.connections ? `Remover e desconectar ${cfg.connections} banco(s)` : 'Confirmar remoção'}
                    onConfirm={() => act(() => api.del<Saved>('/pluggy/config'), done)} />
                </div>
              </div>
            )}
          </Panel>
          <HowTo />
          </div>
        </div>
      )}
    </div>
  )
}
