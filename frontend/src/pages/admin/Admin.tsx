import { useEffect, useState } from 'react'
import { Badge, Button, ConfirmButton, ErrorBox, Field, Input, Modal, PageHeader, Panel, Select, Spinner, Table } from '../../components/ui'
import { api, tokens, type ApiError } from '../../lib/api'
import { CodeInput, DisableModal, EnableModal, type TwoFactorClient } from '../../components/TwoFactor'
import { dateBR } from '../../lib/format'
import { useApi } from '../../lib/hooks'
import { navigate } from '../../lib/router'
import { useSession } from '../../lib/session'
import type { Tenant } from '../../lib/types'

interface Member {
  id: number; name: string; email: string; last_seen: string | null; online: boolean
  two_factor_enabled: boolean; two_factor_locked: boolean; two_factor_failures: number
}
interface AdminTenant extends Tenant { code: string; created_at: string; members: Member[] }
interface AdminData {
  admin: { name: string }
  counts: Record<'tenants' | 'users' | 'trial_active' | 'expired' | 'mensal' | 'anual' | 'vitalicio' | 'expiring_soon', number>
  tenants: AdminTenant[]; online: Member[]
}

const COUNT_LABELS: [keyof AdminData['counts'], string][] = [
  ['tenants', 'Famílias'], ['users', 'Usuários'], ['trial_active', 'Em trial'], ['mensal', 'Mensal'],
  ['anual', 'Anual'], ['vitalicio', 'Vitalício'], ['expiring_soon', 'Vencem em 7 dias'], ['expired', 'Expiradas'],
]

const ADMIN_2FA: TwoFactorClient = { base: '/admin/2fa', post: (path, body) => api.admin.post(path, body) }
type AdminLogin = { access_token: string } | { two_factor_required: true; challenge: string }

export function AdminLoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [challenge, setChallenge] = useState<string | null>(null)  // senha certa, falta o código do app
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const enter = (token: string) => { tokens.setAdmin(token); navigate('/admin', { replace: true }) }
  async function attempt(task: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await task() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault()
    attempt(async () => {
      const r = await api.post<AdminLogin>('/admin/login', { email, password })
      if ('two_factor_required' in r) { setChallenge(r.challenge); setCode('') } else enter(r.access_token)
    })
  }
  const submitCode = (e: React.FormEvent) => {
    e.preventDefault()
    attempt(async () => {
      try {
        enter((await api.post<{ access_token: string }>('/admin/login/2fa', { challenge, code })).access_token)
      } catch (err) {
        if ((err as ApiError).code === '2fa_locked') setChallenge(null)
        setCode('')
        throw err
      }
    })
  }

  return (
    <div className="auth auth-admin">
      <div className="auth-main">
        <div className="auth-card">
          <h1>Administração</h1>
          {challenge ? (
            <form className="stack" onSubmit={submitCode}>
              <p className="muted">Digite o código de 6 dígitos do app autenticador.</p>
              <Field label="Código do app"><CodeInput value={code} onChange={setCode} /></Field>
              {error && <p className="form-error" role="alert">{error}</p>}
              <Button type="submit" busy={busy} className="btn-block">Confirmar</Button>
              <button type="button" className="link-button" onClick={() => { setChallenge(null); setError(null) }}>Voltar</button>
            </form>
          ) : (
            <form className="stack" onSubmit={submitPassword}>
              <Field label="E-mail"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
              <Field label="Senha"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
              {error && <p className="form-error" role="alert">{error}</p>}
              <Button type="submit" busy={busy} className="btn-block">Entrar</Button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}

/** Botão no topo do painel: ativa/desativa o 2FA do próprio administrador. */
function AdminTwoFactor() {
  const { toast } = useSession()
  const status = useApi(() => api.admin.get<{ enabled: boolean }>('/admin/2fa'), [])
  const [modal, setModal] = useState<'enable' | 'disable' | null>(null)
  const done = (message: string) => { setModal(null); status.reload(); toast(message) }
  if (!status.data) return null
  const on = status.data.enabled
  return (
    <>
      <Button variant="ghost" icon={on ? 'shield-check' : 'shield'} onClick={() => setModal(on ? 'disable' : 'enable')}
        title={on ? 'Desativar a verificação em duas etapas' : 'Ativar a verificação em duas etapas'}>
        {on ? '2FA ativo' : 'Ativar 2FA'}
      </Button>
      <EnableModal open={modal === 'enable'} client={ADMIN_2FA} onClose={() => setModal(null)}
        onDone={() => done('Verificação em duas etapas ativada no login do administrador.')} />
      <DisableModal open={modal === 'disable'} client={ADMIN_2FA} onClose={() => setModal(null)}
        onDone={() => done('Verificação em duas etapas desativada.')} />
    </>
  )
}

export function AdminPage() {
  const { toast } = useSession()
  const data = useApi(() => (tokens.admin() ? api.admin.get<AdminData>('/admin/dashboard') : Promise.reject(new Error('Faça login.'))), [])
  const [reset, setReset] = useState<Member | null>(null)
  const [newPwd, setNewPwd] = useState('')

  useEffect(() => { if (!tokens.admin()) navigate('/admin/login', { replace: true }) }, [])
  const run = async (fn: () => Promise<{ message: string }>) => {
    try { const r = await fn(); toast(r.message); data.reload() } catch (e) { toast((e as Error).message, 'danger') }
  }
  const logout = () => { tokens.setAdmin(null); navigate('/admin/login', { replace: true }) }
  const d = data.data

  return (
    <div className="admin">
      <header className="admin-top"><b>FinFam · Administração</b>
        <span className="row gap">{tokens.admin() && <AdminTwoFactor />}<Button variant="ghost" onClick={logout}>Sair</Button></span></header>
      <div className="page">
        {data.error ? <ErrorBox message={data.error} onRetry={logout} /> : !d ? <Spinner /> : (
          <>
            <PageHeader title="Famílias e planos" subtitle={`${d.online.length} pessoa(s) online agora`} />
            <dl className="kpis kpis-inline kpis-wrap">
              {COUNT_LABELS.map(([k, label]) => <div key={k}><dt>{label}</dt><dd>{d.counts[k]}</dd></div>)}
            </dl>
            <Panel flush>
              <Table>
                  <thead><tr><th>Família</th><th>Pessoas</th><th>Plano</th><th>Validade</th><th>Ações</th></tr></thead>
                  <tbody>
                    {d.tenants.map((t) => (
                      <tr key={t.id}>
                        <td><b>{t.name}</b><small className="muted block">desde {dateBR(t.created_at)}</small></td>
                        <td>
                          {t.members.map((m) => (
                            <div key={m.id} className="admin-member">
                              <span className={`dot ${m.online ? 'dot-on' : ''}`} aria-label={m.online ? 'online' : 'offline'} />
                              {m.name} <small className="muted">{m.email}</small>
                              {m.two_factor_locked ? <Badge tone="danger">bloqueado (2FA)</Badge>
                                : m.two_factor_enabled ? <Badge tone="success">2FA</Badge> : null}
                              {m.two_factor_failures > 0 && !m.two_factor_locked && <small className="muted">{m.two_factor_failures} erro(s) 2FA</small>}
                              <button className="link-btn" onClick={() => { setReset(m); setNewPwd('') }}>senha</button>
                              {(m.two_factor_locked || m.two_factor_failures > 0) && (
                                <button className="link-btn" onClick={() => run(() => api.admin.post(`/admin/users/${m.id}/2fa/unlock`))}>zerar erros 2FA</button>
                              )}
                              {m.two_factor_enabled && (
                                <ConfirmButton label={`Remover 2FA de ${m.name}`} icon="shield" text="remover 2FA" confirmLabel="Confirmar remoção do 2FA"
                                  onConfirm={() => run(() => api.admin.del(`/admin/users/${m.id}/2fa`))} />
                              )}
                              <ConfirmButton label={`Excluir ${m.name}`} confirmLabel="Excluir" onConfirm={() => run(() => api.admin.del(`/admin/users/${m.id}`))} />
                            </div>
                          ))}
                          <small className="muted">{t.members.length}/{t.member_limit}{t.extra_members ? ` (+${t.extra_members} extra)` : ''}</small>
                        </td>
                        <td>
                          <Select aria-label="Plano" value={t.plan} onChange={(e) => run(() => api.admin.post(`/admin/tenants/${t.id}/plan`, { plan: e.target.value }))}
                            options={[{ value: 'trial', label: 'Trial' }, { value: 'mensal', label: 'Mensal' }, { value: 'anual', label: 'Anual' }, { value: 'vitalicio', label: 'Vitalício' }]} />
                        </td>
                        <td>{t.expires_at ? <>{dateBR(t.expires_at)} {!t.active ? <Badge tone="danger">expirado</Badge> : t.days_left !== null && t.days_left <= 7 ? <Badge tone="warning">{t.days_left}d</Badge> : null}</> : <Badge tone="success">sem prazo</Badge>}</td>
                        <td className="actions">
                          {(t.plan === 'anual' || t.plan === 'vitalicio') && <Button variant="ghost" onClick={() => run(() => api.admin.post(`/admin/tenants/${t.id}/extra-members`))}>+1 pessoa</Button>}
                          {t.extra_members > 0 && <Button variant="ghost" onClick={() => run(() => api.admin.del(`/admin/tenants/${t.id}/extra-members`))}>−1 pessoa</Button>}
                          <ConfirmButton label="Revogar acesso" icon="x-circle" confirmLabel="Revogar" onConfirm={() => run(() => api.admin.post(`/admin/tenants/${t.id}/revoke`))} />
                          <ConfirmButton label="Excluir família" confirmLabel="Excluir tudo" onConfirm={() => run(() => api.admin.del(`/admin/tenants/${t.id}`))} />
                        </td>
                      </tr>))}
                  </tbody>
                </Table>
            </Panel>
          </>
        )}
      </div>
      <Modal open={Boolean(reset)} onClose={() => setReset(null)} title={`Nova senha para ${reset?.name ?? ''}`}
        footer={<><Button variant="ghost" onClick={() => setReset(null)}>Cancelar</Button>
          <Button disabled={newPwd.length < 8} onClick={() => reset && run(() => api.admin.post(`/admin/users/${reset.id}/reset-password`, { new_password: newPwd })).then(() => setReset(null))}>Redefinir senha</Button></>}>
        <Field label="Nova senha" hint="Mínimo de 8 caracteres"><Input type="text" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} /></Field>
      </Modal>
    </div>
  )
}
