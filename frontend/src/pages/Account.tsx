import { useState } from 'react'
import { Avatar, Badge, Button, Check, ConfirmButton, Empty, ErrorBox, Field, IconButton, Input, Modal, PageHeader,
  Panel, Select, Spinner, Table } from '../components/ui'
import { api, tokens as authTokens } from '../lib/api'
import { dateBR } from '../lib/format'
import { useApi } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import type { Tenant, User } from '../lib/types'

interface FamilyData { tenant: Tenant; members: User[]; member_count: number; member_limit: number; owner_id: number | null }

export function FamilyPage() {
  const { me, refreshMe, toast } = useSession()
  const act = useAction()
  const data = useApi(() => api.get<FamilyData>('/family'), [])
  const [name, setName] = useState<string | null>(null)
  const [modal, setModal] = useState<{ open: boolean; member: User | null }>({ open: false, member: null })
  const [f, setF] = useState({ name: '', email: '', password: '', confirm_password: '', current_password: '' })
  const [error, setError] = useState<string | null>(null)
  const d = data.data
  const full = d ? d.member_count >= d.member_limit : false
  const iAmOwner = Boolean(d && me && d.owner_id === me.user.id)
  const editingSelf = Boolean(modal.member && modal.member.id === me?.user.id)
  // regras da API: só o dono troca e-mail/senha de outra pessoa; a própria troca pede a senha atual
  const canChangeCredentials = !modal.member || editingSelf || iAmOwner
  const needsCurrent = editingSelf && (Boolean(f.password) || f.email.trim().toLowerCase() !== (modal.member?.email ?? ''))

  const openMember = (member: User | null) => {
    setError(null)
    setF({ name: member?.name ?? '', email: member?.email ?? '', password: '', confirm_password: '', current_password: '' })
    setModal({ open: true, member })
  }

  async function saveMember(e: React.FormEvent) {
    e.preventDefault()
    try {
      const r = modal.member
        ? await api.put<{ message: string }>(`/family/members/${modal.member.id}`, { name: f.name, email: f.email,
          new_password: f.password || null, confirm_password: f.confirm_password || null,
          current_password: needsCurrent ? f.current_password : null })
        : await api.post<{ message: string }>('/family/members', { name: f.name, email: f.email, password: f.password,
          confirm_password: f.confirm_password })
      toast(r.message)
      setModal({ open: false, member: null })
      data.reload()
      refreshMe()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="page">
      <PageHeader title="Família" subtitle={d && <>Plano {d.tenant.plan_label}: {d.member_count} de {d.member_limit} pessoa(s)</>}
        actions={<Button icon="plus" disabled={full} title={full ? 'Limite do plano atingido' : undefined} onClick={() => openMember(null)}>Adicionar pessoa</Button>} />
      {data.error ? <ErrorBox message={data.error} /> : !d ? <Spinner /> : (
        <div className="grid-2">
          <Panel title="Nome do grupo">
            <form className="inline-form" onSubmit={(e) => { e.preventDefault(); act(() => api.patch<{ message: string }>('/family', { name: name ?? d.tenant.name }), () => { data.reload(); refreshMe() }) }}>
              <Field label="Nome"><Input value={name ?? d.tenant.name} onChange={(e) => setName(e.target.value)} minLength={2} maxLength={60} required /></Field>
              <Button type="submit" variant="ghost">Salvar nome</Button>
            </form>
          </Panel>
          <Panel title="Pessoas" flush>
            <ul className="member-list">
              {d.members.map((m) => (
                <li key={m.id}>
                  <Avatar name={m.name} url={m.avatar_url} color={m.color} />
                  <div><b>{m.name}</b>{m.id === me?.user.id && <Badge tone="info">você</Badge>}<small className="muted block">{m.email}</small></div>
                  <span className="actions">
                    <IconButton icon="pencil" label="Editar" onClick={() => openMember(m)} />
                    {m.id !== me?.user.id && iAmOwner && <ConfirmButton label="Remover" confirmLabel="Remover e apagar dados"
                      onConfirm={() => act(() => api.del<{ message: string }>(`/family/members/${m.id}`), () => { data.reload(); refreshMe() })} />}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
      <Modal open={modal.open} onClose={() => setModal({ open: false, member: null })} title={modal.member ? 'Editar pessoa' : 'Adicionar pessoa'}
        footer={<><Button variant="ghost" onClick={() => setModal({ open: false, member: null })}>Cancelar</Button><Button type="submit" form="member-form">Salvar</Button></>}>
        <form id="member-form" className="stack" onSubmit={saveMember}>
          <Field label="Nome"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required minLength={2} maxLength={30} /></Field>
          <Field label="E-mail (login)" hint={canChangeCredentials ? undefined : 'Só quem criou a família altera o e-mail e a senha de outra pessoa.'}>
            <Input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required disabled={!canChangeCredentials} />
          </Field>
          {canChangeCredentials && <>
            <Field label={modal.member ? 'Nova senha (opcional)' : 'Senha'} hint="Mínimo de 8 caracteres">
              <Input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required={!modal.member} minLength={8} />
            </Field>
            <Field label="Confirmar senha"><Input type="password" autoComplete="new-password" value={f.confirm_password} onChange={(e) => setF({ ...f, confirm_password: e.target.value })} required={!modal.member || Boolean(f.password)} /></Field>
          </>}
          {needsCurrent && (
            <Field label="Sua senha atual" hint="Necessária para trocar seu e-mail ou sua senha.">
              <Input type="password" autoComplete="current-password" value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} required />
            </Field>
          )}
          {error && <p className="form-error" role="alert">{error}</p>}
        </form>
      </Modal>
    </div>
  )
}

interface ApiToken { id: number; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked: boolean }

export function SettingsPage() {
  const { me, refreshMe, toast } = useSession()
  const act = useAction()
  const tokens = useApi(() => api.get<ApiToken[]>('/auth/tokens'), [])
  const [pwd, setPwd] = useState({ current_password: '', new_password: '', confirm_password: '' })
  const [tokenName, setTokenName] = useState('Bot do Telegram')
  const [newToken, setNewToken] = useState<string | null>(null)

  async function uploadAvatar(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    await act(() => api.post<{ message: string }>('/auth/avatar', fd), () => refreshMe())
  }

  async function createToken(e: React.FormEvent) {
    e.preventDefault()
    try {
      const r = await api.post<{ token: string }>('/auth/tokens', { name: tokenName })
      setNewToken(r.token)
      tokens.reload()
    } catch (err) {
      toast((err as Error).message, 'danger')
    }
  }

  return (
    <div className="page">
      <PageHeader title="Perfil e acesso" />
      <div className="grid-2">
        <Panel title="Foto">
          <div className="row gap">
            {me && <Avatar name={me.user.name} url={me.user.avatar_url} size={64} />}
            <label className="btn btn-ghost">
              Trocar foto
              <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(e) => e.target.files?.[0] && uploadAvatar(e.target.files[0])} />
            </label>
          </div>
        </Panel>
        <Panel title="Senha">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); act(() => api.post<{ message: string; access_token: string }>('/auth/change-password', pwd), (r) => {
              authTokens.setUser(r.access_token)  // a sessão antiga deixa de valer ao trocar a senha
              setPwd({ current_password: '', new_password: '', confirm_password: '' })
            }) }}>
            <Field label="Senha atual"><Input type="password" autoComplete="current-password" value={pwd.current_password} onChange={(e) => setPwd({ ...pwd, current_password: e.target.value })} required /></Field>
            <Field label="Nova senha" hint="Mínimo de 8 caracteres"><Input type="password" autoComplete="new-password" value={pwd.new_password} onChange={(e) => setPwd({ ...pwd, new_password: e.target.value })} required minLength={8} /></Field>
            <Field label="Confirmar nova senha"><Input type="password" autoComplete="new-password" value={pwd.confirm_password} onChange={(e) => setPwd({ ...pwd, confirm_password: e.target.value })} required /></Field>
            <Button type="submit">Trocar senha</Button>
          </form>
        </Panel>
      </div>
      <Panel title="Tokens de API (MCP e bot do Telegram)">
        <p className="muted">Um token dá acesso aos dados da sua família pela API, em seu nome. Use um token por integração e revogue o que não usar mais.</p>
        <form className="inline-form" onSubmit={createToken}>
          <Field label="Nome do token"><Input value={tokenName} onChange={(e) => setTokenName(e.target.value)} required minLength={2} maxLength={60} /></Field>
          <Button type="submit" icon="key">Gerar token</Button>
        </form>
        {newToken && (
          <div className="token-reveal" role="status">
            <p><b>Copie agora</b> — este token não será mostrado de novo.</p>
            <code>{newToken}</code>
            <Button variant="ghost" onClick={() => { navigator.clipboard?.writeText(newToken); toast('Token copiado.') }}>Copiar</Button>
          </div>
        )}
        {tokens.data && tokens.data.length > 0 ? (
          <Table>
            <thead><tr><th>Nome</th><th>Início</th><th>Criado</th><th>Último uso</th><th /></tr></thead>
            <tbody>{tokens.data.map((t) => (
              <tr key={t.id} className={t.revoked ? 'row-off' : ''}>
                <td>{t.name}</td><td><code>{t.prefix}…</code></td><td>{dateBR(t.created_at)}</td><td>{t.last_used_at ? dateBR(t.last_used_at) : 'nunca'}</td>
                <td>{t.revoked ? <Badge>Revogado</Badge> : <ConfirmButton label="Revogar" confirmLabel="Revogar" onConfirm={() => act(() => api.del<{ message: string }>(`/auth/tokens/${t.id}`), tokens.reload)} />}</td>
              </tr>))}
            </tbody>
          </Table>
        ) : tokens.data && <Empty icon="key" title="Nenhum token criado." />}
      </Panel>
    </div>
  )
}

interface LinkStatus {
  bot_configured: boolean; bot_username: string | null; linked: boolean; telegram_username: string | null
  members: { id: number; name: string; linked: boolean }[]
}
interface TelegramData extends LinkStatus { enabled: boolean; hour: number; minute: number; configured: boolean; legacy_configured: boolean }
interface AiProvider { id: string; label: string; default_model: string; key_url: string }
interface AiData { configured: boolean; enabled: boolean; provider: string | null; model: string | null; base_url: string | null; key_masked: string; providers: AiProvider[] }

const HOURS = Array.from({ length: 24 }, (_, i) => ({ value: String(i), label: `${String(i).padStart(2, '0')}h` }))
const MINUTES = Array.from({ length: 12 }, (_, i) => ({ value: String(i * 5), label: String(i * 5).padStart(2, '0') }))

export function TelegramPage() {
  const data = useApi(() => api.get<TelegramData>('/settings/telegram'), [])
  const d = data.data
  return (
    <div className="page">
      <PageHeader title="Telegram e IA" subtitle="Consulte contas, pague e lance gastos conversando com o bot do FinFam." />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : (
        <div className="grid-2">
          <TelegramLinkPanel d={d} reload={data.reload} />
          <ReminderPanel d={d} reload={data.reload} />
          <AiPanel />
          <Panel title="O que o bot faz">
            <ul className="plain-list">
              <li><code>/contas</code> contas a pagar, com botão para marcar como paga</li>
              <li><code>/resumo</code> resumo do mês</li>
              <li><code>/lancar</code> lançar um gasto passo a passo</li>
              <li>Com a IA ligada, é só escrever: <i>gastei 87,50 no mercado no cartão Inter em 3x</i></li>
            </ul>
            <p className="muted">Nada é lançado sem você confirmar no ✅. Cada pessoa conecta o próprio Telegram e vê só os dados da família dela.</p>
          </Panel>
        </div>
      )}
    </div>
  )
}

function TelegramLinkPanel({ d, reload }: { d: TelegramData; reload: () => void }) {
  const act = useAction()
  const [link, setLink] = useState<{ url: string; expires_at: string } | null>(null)
  if (!d.bot_configured) {
    return (
      <Panel title="Seu Telegram">
        <p className="muted">O bot do FinFam ainda não foi ativado neste servidor. O administrador precisa definir <code>TELEGRAM_BOT_TOKEN</code> e <code>TELEGRAM_BOT_USERNAME</code>.</p>
      </Panel>
    )
  }
  return (
    <Panel title="Seu Telegram">
      <div className="stack">
        {d.linked ? (
          <>
            <p><Badge tone="success">Conectado</Badge>{d.telegram_username && <> como <b>@{d.telegram_username}</b></>}</p>
            <p className="muted">Abra <a href={`https://t.me/${d.bot_username}`} target="_blank" rel="noreferrer">@{d.bot_username}</a> no Telegram e mande <code>/contas</code>.</p>
            <div><ConfirmButton label="Desconectar Telegram" text="Desconectar" confirmLabel="Confirmar desconexão" icon="trash"
              onConfirm={() => act(() => api.del<{ message: string }>('/telegram/link'), () => { setLink(null); reload() })} /></div>
          </>
        ) : link ? (
          <>
            <a className="btn btn-primary" href={link.url} target="_blank" rel="noreferrer">Abrir o Telegram e conectar</a>
            <p className="muted">No Telegram, toque em <b>Iniciar</b>. O link vale 15 minutos e só funciona uma vez. Depois, <button type="button" className="link-button" onClick={reload}>atualize esta página</button>.</p>
          </>
        ) : (
          <>
            <p className="muted">Conecte o seu Telegram ao <b>@{d.bot_username}</b> para consultar e lançar pelo celular.</p>
            <div><Button icon="telegram" onClick={() => act(() => api.post<{ url: string; expires_at: string; message?: string }>('/telegram/link-code'), setLink)}>Conectar Telegram</Button></div>
          </>
        )}
        {d.members.length > 1 && (
          <p className="muted">Na família: {d.members.map((m, i) => <span key={m.id}>{i > 0 && ' · '}{m.name} {m.linked ? '✅' : '(não conectado)'}</span>)}</p>
        )}
      </div>
    </Panel>
  )
}

function ReminderPanel({ d, reload }: { d: TelegramData; reload: () => void }) {
  const act = useAction()
  const [f, setF] = useState<{ enabled: boolean; hour: string; minute: string } | null>(null)
  const form = f ?? { enabled: d.enabled, hour: String(d.hour), minute: String(d.minute) }
  return (
    <Panel title="Lembrete diário">
      <form className="stack" onSubmit={(e) => {
        e.preventDefault()
        act(() => api.put<{ message: string }>('/settings/telegram', { enabled: form.enabled, hour: Number(form.hour), minute: Number(form.minute) }),
          () => { setF(null); reload() })
      }}>
        <p className="muted">Todo dia, no horário escolhido, quem conectou o Telegram recebe as contas do dia, as atrasadas e os alertas da família.</p>
        <Check label="Enviar lembrete todos os dias" checked={form.enabled} onChange={(v) => setF({ ...form, enabled: v })} />
        <div className="row gap">
          <Field label="Hora"><Select value={form.hour} onChange={(e) => setF({ ...form, hour: e.target.value })} options={HOURS} /></Field>
          <Field label="Minuto"><Select value={form.minute} onChange={(e) => setF({ ...form, minute: e.target.value })} options={MINUTES} /></Field>
        </div>
        {d.legacy_configured && !d.members.some((m) => m.linked) && (
          <p className="muted">Enquanto ninguém conectar o Telegram, o lembrete continua saindo pelo bot próprio configurado no FinFam antigo.</p>
        )}
        <div className="row gap">
          <Button type="submit">Salvar</Button>
          <Button type="button" variant="ghost" icon="telegram" disabled={!d.configured}
            title={d.configured ? undefined : 'Conecte o Telegram primeiro'}
            onClick={() => act(() => api.post<{ message: string }>('/settings/telegram/test'))}>Enviar teste agora</Button>
        </div>
      </form>
    </Panel>
  )
}

function AiPanel() {
  const act = useAction()
  const data = useApi(() => api.get<AiData>('/settings/ai'), [])
  const [f, setF] = useState<{ provider: string; api_key: string; model: string; base_url: string; enabled: boolean } | null>(null)
  const d = data.data
  if (data.error) return <Panel title="Inteligência artificial (opcional)"><ErrorBox message={data.error} onRetry={data.reload} /></Panel>
  if (!d) return <Panel title="Inteligência artificial (opcional)"><Spinner /></Panel>
  const form = f ?? { provider: d.provider ?? 'gemini', api_key: '', model: d.model ?? '', base_url: d.base_url ?? '', enabled: d.configured ? d.enabled : true }
  const provider = d.providers.find((p) => p.id === form.provider) ?? d.providers[0]
  const sameProvider = d.configured && d.provider === form.provider
  const done = () => { setF(null); data.reload() }
  return (
    <Panel title="Inteligência artificial (opcional)">
      <form className="stack" onSubmit={(e) => {
        e.preventDefault()
        act(() => api.put<{ message: string }>('/settings/ai', { provider: form.provider, api_key: form.api_key || null,
          model: form.model || null, base_url: form.provider === 'openai' ? form.base_url || null : null, enabled: form.enabled }), done)
      }}>
        <p className="muted">Com a IA, o bot entende frases como <i>"paguei 120 de luz ontem no PIX"</i>. A família usa a <b>própria chave</b> e paga direto ao provedor (para lançar gastos, costuma ser centavos por mês).</p>
        <Field label="Provedor">
          <Select value={form.provider} onChange={(e) => setF({ ...form, provider: e.target.value, model: '', api_key: '' })}
            options={d.providers.map((p) => ({ value: p.id, label: p.label }))} />
        </Field>
        <Field label="Chave de API" hint={<>
          {sameProvider ? `Atual: ${d.key_masked}. Deixe em branco para manter. ` : ''}
          <a href={provider.key_url} target="_blank" rel="noreferrer">Onde pego a chave?</a>
          {form.provider === 'gemini' && ' O Gemini tem cota gratuita.'}
        </>}>
          <Input type="password" value={form.api_key} onChange={(e) => setF({ ...form, api_key: e.target.value })}
            autoComplete="off" required={!sameProvider} placeholder={sameProvider ? d.key_masked : 'Cole a chave aqui'} />
        </Field>
        <Field label="Modelo" hint={`Em branco: ${provider.default_model}`}>
          <Input value={form.model} onChange={(e) => setF({ ...form, model: e.target.value })} placeholder={provider.default_model} />
        </Field>
        {form.provider === 'openai' && (
          <Field label="Endereço compatível (opcional)" hint="Para OpenRouter, DeepSeek, Groq etc. Ex.: https://openrouter.ai/api/v1. Em branco: OpenAI.">
            <Input value={form.base_url} onChange={(e) => setF({ ...form, base_url: e.target.value })} placeholder="https://…" />
          </Field>
        )}
        <Check label="Usar IA no bot" checked={form.enabled} onChange={(v) => setF({ ...form, enabled: v })} />
        <p className="muted">Assinaturas como Claude Pro, ChatGPT Plus ou Gemini Advanced <b>não</b> incluem API: a chave é criada à parte no site do provedor.</p>
        <div className="row gap">
          <Button type="submit">Salvar</Button>
          {d.configured && <>
            <Button type="button" variant="ghost" icon="stars" onClick={() => act(() => api.post<{ message: string }>('/settings/ai/test'))}>Testar</Button>
            <ConfirmButton label="Remover chave de IA" text="Remover chave" confirmLabel="Confirmar remoção" onConfirm={() => act(() => api.del<{ message: string }>('/settings/ai'), done)} />
          </>}
        </div>
      </form>
    </Panel>
  )
}

export function UpgradePage({ expired }: { expired?: boolean }) {
  const { logout } = useSession()
  const data = useApi(() => api.get<{ current: Tenant | null; plans: { id: string; label: string; members: number; period_days: number | null }[]; support_email: string }>('/plans'), [])
  const d = data.data
  return (
    <div className="page">
      <PageHeader title={expired ? 'Seu acesso expirou' : 'Planos'}
        subtitle={d?.current && <>Plano atual: <b>{d.current.plan_label}</b>{d.current.expires_at ? ` · válido até ${dateBR(d.current.expires_at)}` : ''}</>}
        actions={expired && <Button variant="ghost" onClick={logout}>Sair</Button>} />
      {expired && <p className="lead">Seus dados continuam guardados. Escolha um plano para voltar a usar o FinFam.</p>}
      {!d ? <Spinner /> : (
        <div className="plan-grid">
          {d.plans.map((p) => {
            const current = d.current?.plan === p.id && d.current.active
            return (
              <article key={p.id} className={`plan ${current ? 'plan-current' : ''}`}>
                <h2>{p.label}</h2>
                <p className="plan-members">{p.members} {p.members > 1 ? 'pessoas' : 'pessoa'} na família</p>
                <p className="muted">{p.period_days ? `Acesso por ${p.period_days} dias` : 'Acesso sem prazo'}</p>
                {current ? <Badge tone="success">Plano atual</Badge>
                  : <a className="btn btn-primary" href={`mailto:${d.support_email}?subject=${encodeURIComponent(`Quero o plano ${p.label}`)}`}>Pedir plano {p.label}</a>}
              </article>
            )
          })}
        </div>
      )}
      {d && <p className="muted">A contratação é feita pelo e-mail <a href={`mailto:${d.support_email}`}>{d.support_email}</a>.</p>}
    </div>
  )
}
