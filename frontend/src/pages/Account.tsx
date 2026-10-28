import { useState } from 'react'
import { Avatar, Badge, Button, ConfirmButton, ErrorBox, Field, IconButton, Input, Modal, PageHeader,
  Panel, Spinner } from '../components/ui'
import { TwoFactorPanel } from '../components/TwoFactor'
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
                  <div><b>{m.name}</b>{m.id === me?.user.id && <Badge tone="info">você</Badge>}<small className="muted block">{m.email}</small>
                    {m.email_verified === false && <span className="small">
                      <Badge tone="warning">aguardando confirmar o e-mail</Badge>{' '}
                      <button type="button" className="link-btn" onClick={() => act(() => api.post<{ message: string }>(`/family/members/${m.id}/resend`))}>reenviar</button>
                    </span>}</div>
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

export function SettingsPage() {
  const { me, refreshMe } = useSession()
  const act = useAction()
  const [pwd, setPwd] = useState({ current_password: '', new_password: '', confirm_password: '' })

  async function uploadAvatar(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    await act(() => api.post<{ message: string }>('/auth/avatar', fd), () => refreshMe())
  }

  return (
    <div className="page">
      <PageHeader title="Perfil e acesso" />
      <div className="grid-2">
        <div className="stack-lg">
        <Panel title="Foto">
          <div className="row gap">
            {me && <Avatar name={me.user.name} url={me.user.avatar_url} size={64} />}
            <label className="btn btn-ghost">
              Trocar foto
              <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(e) => e.target.files?.[0] && uploadAvatar(e.target.files[0])} />
            </label>
          </div>
        </Panel>
        <TwoFactorPanel />
        </div>
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
    </div>
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
