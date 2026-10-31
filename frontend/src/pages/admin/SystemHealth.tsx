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

const BACKUP_STALE_HOURS = 30
const MONITOR_STALE_HOURS = 0.5

function MonitorStatus({ monitor }: { monitor: Health['monitor'] }) {
  const stale = hoursAgo(monitor.checked_at) > MONITOR_STALE_HOURS
  return (
    <>
      <p>
        <b>Monitor: </b>
        {!monitor.configured ? <Badge tone="warning">não configurado</Badge>
          : stale ? <Badge tone="warning">sem checar desde {when(monitor.checked_at)}</Badge>
            : monitor.problems.length ? <Badge tone="danger">{monitor.problems.length} problema(s)</Badge>
              : <Badge tone="success">tudo ok</Badge>}
        <span className="muted small"> · última checagem {when(monitor.checked_at)}</span>
      </p>
      {monitor.problems.map((p) => <p key={p.check} className="small">• {p.problem} <span className="muted">(desde {when(p.since)})</span></p>)}
    </>
  )
}

function BackupStatus({ backup }: { backup: Health['backup'] }) {
  const late = hoursAgo(backup.last_ok) > BACKUP_STALE_HOURS
  return (
    <>
      <p>
        <b>Backup fora do Raspberry: </b>
        {!backup.configured ? <Badge tone="warning">não configurado</Badge>
          : late ? <Badge tone="danger">atrasado</Badge> : <Badge tone="success">em dia</Badge>}
        <span className="muted small"> · último ok {when(backup.last_ok)}
          {backup.destinations.length > 0 && ` → ${backup.destinations.join(', ')}`}
          {backup.size ? ` · ${Math.round(backup.size / 1024)} KB` : ''}</span>
      </p>
      {backup.errors.map((e) => <p key={e} className="small form-error">• {e}</p>)}
    </>
  )
}

export function SystemHealth() {
  const h = useApi(() => api.admin.get<Health>('/admin/health'), []).data
  if (!h) return null
  return (
    <Panel title="Saúde do sistema">
      <div className="stack-sm">
        <MonitorStatus monitor={h.monitor} />
        <BackupStatus backup={h.backup} />
        {(!h.monitor.configured || !h.backup.configured) && (
          <p className="muted small">Configure no backend/.env do Raspberry e no cron — veja o CHANGELOG da versão 2026.9.0.</p>
        )}
      </div>
    </Panel>
  )
}
