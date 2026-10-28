// Admin › Famílias e planos: a lista é o "livro de famílias". Os números do topo são os filtros;
// cada família mostra de cara só o que decide (quem, plano, validade, sinais) e abre para gerenciar.
import { useMemo, useState } from 'react'
import { Avatar, Badge, Button, ConfirmButton, Input, Select, Switch } from '../../components/ui'
import { Icon } from '../../components/Icon'
import { api } from '../../lib/api'
import { dateBR } from '../../lib/format'
import type { Tenant } from '../../lib/types'

export interface Member {
  id: number; name: string; email: string; last_seen: string | null; online: boolean
  two_factor_enabled: boolean; two_factor_locked: boolean; two_factor_failures: number; email_verified?: boolean
}
export interface AdminTenant extends Tenant {
  code: string; created_at: string; members: Member[]; pluggy: { configured: number; errors: number }
}
type Run = (fn: () => Promise<{ message: string }>) => Promise<void>

const PLANS = [{ value: 'trial', label: 'Trial' }, { value: 'mensal', label: 'Mensal' },
  { value: 'anual', label: 'Anual' }, { value: 'vitalicio', label: 'Vitalício' }]
const DAY = 86_400_000

/** Validade em palavras, com o tom que decide a cor. */
function validity(t: AdminTenant): { tone: 'expired' | 'soon' | 'ok' | 'life'; text: string } {
  if (!t.expires_at) return { tone: 'life', text: 'Sem prazo' }
  const days = Math.ceil((new Date(t.expires_at).getTime() - Date.now()) / DAY)
  if (!t.active || days < 0) return { tone: 'expired', text: days < -1 ? `Expirou há ${-days} dias` : 'Expirou' }
  if (days <= 7) return { tone: 'soon', text: days <= 0 ? 'Vence hoje' : `Vence em ${days} ${days === 1 ? 'dia' : 'dias'}` }
  return { tone: 'ok', text: `Até ${dateBR(t.expires_at)}` }
}

/** O que pede atenção do admin nesta família. */
function issues(t: AdminTenant): string[] {
  const out: string[] = []
  const locked = t.members.filter((m) => m.two_factor_locked).length
  const unconfirmed = t.members.filter((m) => m.email_verified === false).length
  const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`
  if (locked) out.push(n(locked, 'bloqueado no 2FA', 'bloqueados no 2FA'))
  if (unconfirmed) out.push(n(unconfirmed, 'sem confirmar o e-mail', 'sem confirmar o e-mail'))
  if (t.pluggy.errors) out.push(n(t.pluggy.errors, 'chave Pluggy com erro', 'chaves Pluggy com erro'))
  return out
}

type Filter = 'all' | 'attention' | 'expired' | 'soon' | 'trial' | 'mensal' | 'anual' | 'vitalicio'
const FILTERS: { id: Filter; label: string; test: (t: AdminTenant) => boolean }[] = [
  { id: 'all', label: 'Todas', test: () => true },
  { id: 'attention', label: 'Pedem atenção', test: (t) => issues(t).length > 0 },
  { id: 'expired', label: 'Expiradas', test: (t) => validity(t).tone === 'expired' },
  { id: 'soon', label: 'Vencem em 7 dias', test: (t) => validity(t).tone === 'soon' },
  { id: 'trial', label: 'Trial', test: (t) => t.plan === 'trial' },
  { id: 'mensal', label: 'Mensal', test: (t) => t.plan === 'mensal' },
  { id: 'anual', label: 'Anual', test: (t) => t.plan === 'anual' },
  { id: 'vitalicio', label: 'Vitalício', test: (t) => t.plan === 'vitalicio' },
]

/** Os números do topo: cada um é um filtro da lista. */
function FilterBar({ tenants, value, onChange }: { tenants: AdminTenant[]; value: Filter; onChange: (f: Filter) => void }) {
  return (
    <div className="fam-filters" role="radiogroup" aria-label="Filtrar famílias">
      {FILTERS.map((f) => {
        const n = tenants.filter(f.test).length
        return (
          <button key={f.id} type="button" role="radio" aria-checked={value === f.id}
            className={`fam-filter ${value === f.id ? 'on' : ''} ${f.id === 'attention' || f.id === 'expired' ? 'warn' : ''}`}
            onClick={() => onChange(f.id)}>
            <b>{n}</b><span>{f.label}</span>
          </button>
        )
      })}
    </div>
  )
}

function MemberRow({ m, run, onPassword }: { m: Member; run: Run; onPassword: (m: Member) => void }) {
  const post = (path: string) => run(() => api.admin.post(`/admin/users/${m.id}/${path}`))
  return (
    <li className="fam-member">
      <div className="fam-who">
        <span className={`dot ${m.online ? 'dot-on' : ''}`} title={m.online ? 'Online agora' : m.last_seen ? `Visto em ${dateBR(m.last_seen)}` : 'Nunca entrou'} />
        <div>
          <b>{m.name}</b>
          <small className="muted block">{m.email}</small>
          <span className="fam-tags">
            {m.two_factor_locked ? <Badge tone="danger">bloqueado no 2FA</Badge> : m.two_factor_enabled && <Badge tone="success">2FA</Badge>}
            {m.two_factor_failures > 0 && !m.two_factor_locked && <Badge tone="warning">{m.two_factor_failures} erro(s) no 2FA</Badge>}
            {m.email_verified === false && <Badge tone="warning">e-mail não confirmado</Badge>}
          </span>
        </div>
      </div>
      <div className="fam-member-actions">
        <Button variant="ghost" icon="key" onClick={() => onPassword(m)}>Definir senha</Button>
        <Button variant="ghost" onClick={() => post('send-reset')}>Enviar link de senha</Button>
        {m.email_verified === false && <Button variant="ghost" icon="check2" onClick={() => post('verify-email')}>Confirmar e-mail</Button>}
        {(m.two_factor_locked || m.two_factor_failures > 0) && <Button variant="ghost" onClick={() => post('2fa/unlock')}>Desbloquear 2FA</Button>}
        {m.two_factor_enabled && <ConfirmButton label={`Remover 2FA de ${m.name}`} icon="shield" text="Remover 2FA"
          confirmLabel="Confirmar remoção" onConfirm={() => run(() => api.admin.del(`/admin/users/${m.id}/2fa`))} />}
        <ConfirmButton label={`Excluir ${m.name}`} confirmLabel="Excluir pessoa" onConfirm={() => run(() => api.admin.del(`/admin/users/${m.id}`))} />
      </div>
    </li>
  )
}

/** Plano, vagas extras e Open Finance. */
function FamilySettings({ t, run }: { t: AdminTenant; run: Run }) {
  const max = t.open_finance_max_items ?? null
  const saveOf = (enabled: boolean, limit: number | null) =>
    run(() => api.admin.put(`/admin/tenants/${t.id}/open-finance`, { enabled, max_items: limit }))
  const extra = t.plan === 'anual' || t.plan === 'vitalicio'
  return (
    <div className="fam-settings">
      <section>
        <h3>Plano</h3>
        <Select aria-label={`Plano da ${t.name}`} value={t.plan} options={PLANS}
          onChange={(e) => run(() => api.admin.post(`/admin/tenants/${t.id}/plan`, { plan: e.target.value }))} />
        <p className="muted small">{t.members.length} de {t.member_limit} pessoas{t.extra_members ? ` (com ${t.extra_members} extra)` : ''}</p>
        {extra && (
          <div className="row gap">
            <Button variant="ghost" icon="plus" onClick={() => run(() => api.admin.post(`/admin/tenants/${t.id}/extra-members`))}>Vaga extra</Button>
            {t.extra_members > 0 && <Button variant="ghost" onClick={() => run(() => api.admin.del(`/admin/tenants/${t.id}/extra-members`))}>Tirar vaga</Button>}
          </div>
        )}
      </section>
      <section>
        <h3>Open Finance</h3>
        <Switch label={`Open Finance da ${t.name}`} checked={Boolean(t.open_finance)} onChange={(v) => saveOf(v, max)} />
        {t.open_finance && <>
          <Select aria-label={`Bancos por pessoa na ${t.name}`} value={max === null ? '' : String(max)}
            onChange={(e) => saveOf(true, e.target.value ? Number(e.target.value) : null)}
            options={[{ value: '', label: 'Bancos sem limite' }, ...[1, 2, 3, 5, 10].map((n) => ({ value: String(n), label: `${n} banco(s) por pessoa` }))]} />
          <p className="muted small">
            {t.pluggy.configured ? `${t.pluggy.configured} ${t.pluggy.configured === 1 ? 'pessoa com chave' : 'pessoas com chave'}` : 'Ninguém cadastrou a chave ainda'}
            {t.pluggy.errors ? `, ${t.pluggy.errors} com erro` : ''}
          </p>
        </>}
      </section>
    </div>
  )
}

function FamilyRow({ t, run, onPassword, onData }:
  { t: AdminTenant; run: Run; onPassword: (m: Member) => void; onData: (t: AdminTenant) => void }) {
  const [open, setOpen] = useState(false)
  const v = validity(t)
  const problems = issues(t)
  const online = t.members.some((m) => m.online)
  const id = `fam-${t.id}`
  return (
    <li className={`fam fam-${v.tone} ${open ? 'open' : ''}`}>
      <button type="button" className="fam-head" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span className="fam-name">
          <b>{t.name}</b>
          <small className="nowrap">desde {dateBR(t.created_at)}</small>
        </span>
        <span className="fam-people" aria-label={`${t.members.length} de ${t.member_limit} pessoas`}>
          <span className="fam-avatars">{t.members.slice(0, 4).map((m) => <Avatar key={m.id} name={m.name} size={28} />)}</span>
          <small>{t.members.length}/{t.member_limit}{online && <span className="fam-online"> · online</span>}</small>
        </span>
        <span className="fam-plan">
          <b>{t.plan_label}</b>
          <small className={`fam-validity v-${v.tone}`}>{v.text}</small>
        </span>
        <span className="fam-signals">
          {t.open_finance && <span className="fam-signal" title="Open Finance liberado"><Icon name="bank" /> Open Finance</span>}
          {problems.length > 0 && <span className="fam-signal warn" title={problems.join(', ')}><Icon name="exclamation-circle" /> {problems.length === 1 ? problems[0] : `${problems.length} pendências`}</span>}
        </span>
        <span className="fam-toggle"><Icon name="chevron-right" /></span>
      </button>
      {open && (
        <div className="fam-body" id={id}>
          <div className="fam-grid">
            <section>
              <h3>Pessoas</h3>
              <ul className="fam-members">{t.members.map((m) => <MemberRow key={m.id} m={m} run={run} onPassword={onPassword} />)}</ul>
            </section>
            <FamilySettings t={t} run={run} />
          </div>
          <footer className="fam-foot">
            <Button variant="ghost" icon="cloud-upload" onClick={() => onData(t)}>Backup e dados</Button>
            <span className="row gap">
              <ConfirmButton label={`Revogar o acesso da ${t.name}`} icon="x-circle" text="Revogar acesso" confirmLabel="Revogar agora"
                onConfirm={() => run(() => api.admin.post(`/admin/tenants/${t.id}/revoke`))} />
              <ConfirmButton label={`Excluir a ${t.name}`} text="Excluir família" confirmLabel="Excluir tudo"
                onConfirm={() => run(() => api.admin.del(`/admin/tenants/${t.id}`))} />
            </span>
          </footer>
        </div>
      )}
    </li>
  )
}

/** Ordem: o que pede ação primeiro (pendências, expiradas, vencendo), depois as mais novas. */
function rank(t: AdminTenant): number {
  const v = validity(t).tone
  return (issues(t).length ? 0 : 10) + (v === 'expired' ? 0 : v === 'soon' ? 1 : 2)
}

export function FamiliesBoard({ tenants, run, onPassword, onData }:
  { tenants: AdminTenant[]; run: Run; onPassword: (m: Member) => void; onData: (t: AdminTenant) => void }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')
  const list = useMemo(() => {
    const test = FILTERS.find((f) => f.id === filter)!.test
    const needle = q.trim().toLowerCase()
    return tenants
      .filter(test)
      .filter((t) => !needle || t.name.toLowerCase().includes(needle)
        || t.members.some((m) => m.name.toLowerCase().includes(needle) || m.email.toLowerCase().includes(needle)))
      .sort((a, b) => rank(a) - rank(b) || b.created_at.localeCompare(a.created_at))
  }, [tenants, filter, q])

  return (
    <>
      <FilterBar tenants={tenants} value={filter} onChange={setFilter} />
      <div className="fam-search">
        <Input type="search" placeholder="Buscar família, pessoa ou e-mail" aria-label="Buscar família, pessoa ou e-mail"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {list.length === 0
        ? <p className="muted fam-empty">Nenhuma família neste filtro.</p>
        : <ul className="fam-list">{list.map((t) => <FamilyRow key={t.id} t={t} run={run} onPassword={onPassword} onData={onData} />)}</ul>}
    </>
  )
}
