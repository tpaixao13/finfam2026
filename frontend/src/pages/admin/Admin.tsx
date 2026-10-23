import { useEffect, useState } from 'react'
import { Badge, Button, ConfirmButton, ErrorBox, Field, Input, Modal, PageHeader, Panel, Select, Spinner } from '../../components/ui'
import { api, tokens } from '../../lib/api'
import { dateBR } from '../../lib/format'
import { useApi } from '../../lib/hooks'
import { navigate } from '../../lib/router'
import { useSession } from '../../lib/session'
import type { Tenant } from '../../lib/types'

interface Member { id: number; name: string; email: string; last_seen: string | null; online: boolean }
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

export function AdminLoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    try {
      const r = await api.post<{ access_token: string }>('/admin/login', { email, password })
      tokens.setAdmin(r.access_token)
      navigate('/admin', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    }
  }
  return (
    <div className="auth auth-admin">
      <div className="auth-main">
        <div className="auth-card">
          <h1>Administração</h1>
          <form className="stack" onSubmit={submit}>
            <Field label="E-mail"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
            <Field label="Senha"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
            {error && <p className="form-error" role="alert">{error}</p>}
            <Button type="submit" className="btn-block">Entrar</Button>
          </form>
        </div>
      </div>
    </div>
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
      <header className="admin-top"><b>FinFam · Administração</b><Button variant="ghost" onClick={logout}>Sair</Button></header>
      <div className="page">
        {data.error ? <ErrorBox message={data.error} onRetry={logout} /> : !d ? <Spinner /> : (
          <>
            <PageHeader title="Famílias e planos" subtitle={`${d.online.length} pessoa(s) online agora`} />
            <dl className="kpis kpis-inline kpis-wrap">
              {COUNT_LABELS.map(([k, label]) => <div key={k}><dt>{label}</dt><dd>{d.counts[k]}</dd></div>)}
            </dl>
            <Panel flush>
              <div className="table-wrap">
                <table className="table">
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
                              <button className="link-btn" onClick={() => { setReset(m); setNewPwd('') }}>senha</button>
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
                </table>
              </div>
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
