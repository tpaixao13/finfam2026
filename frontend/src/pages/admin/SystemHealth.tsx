// Saúde do sistema no painel do admin: o que o monitor (cron a cada 5 min) e o backup diário escreveram.
import { Badge, Panel } from '../../components/ui'
import { api } from '../../lib/api'
import { useApi } from '../../lib/hooks'

interface Health {
  monitor: { configured: boolean; checked_at: string | null; problems: { check: string; problem: string; since: string | null }[] }
  backup: { configured: boolean; last_ok: string | null; last_run: string | null; destinations: string[]; errors: string[]; size: number | null }
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—')
const hoursAgo = (iso: string | null) => (iso ? (Date.now() - new Date(iso).getTime()) / 36e5 : Infinity)

export function SystemHealth() {
  const data = useApi(() => api.admin.get<Health>('/admin/health'), [])
  const h = data.data
  if (!h) return null
  const monitorOld = hoursAgo(h.monitor.checked_at) > 0.5
  const backupOld = hoursAgo(h.backup.last_ok) > 30
  return (
    <Panel title="Saúde do sistema">
      <div className="stack-sm">
        <p>
          <b>Monitor: </b>
          {!h.monitor.configured ? <Badge tone="warning">não configurado</Badge>
            : monitorOld ? <Badge tone="warning">sem checar desde {when(h.monitor.checked_at)}</Badge>
              : h.monitor.problems.length ? <Badge tone="danger">{h.monitor.problems.length} problema(s)</Badge>
                : <Badge tone="success">tudo ok</Badge>}
          <span className="muted small"> · última checagem {when(h.monitor.checked_at)}</span>
        </p>
        {h.monitor.problems.map((p) => <p key={p.check} className="small">• {p.problem} <span className="muted">(desde {when(p.since)})</span></p>)}
        <p>
          <b>Backup fora do Raspberry: </b>
          {!h.backup.configured ? <Badge tone="warning">não configurado</Badge>
            : backupOld ? <Badge tone="danger">atrasado</Badge> : <Badge tone="success">em dia</Badge>}
          <span className="muted small"> · último ok {when(h.backup.last_ok)}
            {h.backup.destinations.length > 0 && ` → ${h.backup.destinations.join(', ')}`}
            {h.backup.size ? ` · ${Math.round(h.backup.size / 1024)} KB` : ''}</span>
        </p>
        {h.backup.errors.map((e) => <p key={e} className="small form-error">• {e}</p>)}
        {(!h.monitor.configured || !h.backup.configured) && (
          <p className="muted small">Configure no backend/.env do Raspberry e no cron — veja o CHANGELOG da versão 2026.9.0.</p>
        )}
      </div>
    </Panel>
  )
}
