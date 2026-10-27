// Admin: backup completo da família, zerar as despesas e restaurar de um backup.
import { useState } from 'react'
import { Badge, Button, Empty, ErrorBox, Field, Input, Modal, Segmented, Spinner } from '../../components/ui'
import { api } from '../../lib/api'
import { useApi } from '../../lib/hooks'
import { useSession } from '../../lib/session'

interface Backup { name: string; created_at: string | null; reason: string | null; size: number; expenses: number }
type Window = '0' | '1' | '3' | '6' | '12'
const WINDOWS: { value: Window; label: string }[] = [
  { value: '0', label: 'Não trazer' }, { value: '1', label: '1 mês' }, { value: '3', label: '3 meses' },
  { value: '6', label: '6 meses' }, { value: '12', label: '12 meses' },
]

function when(iso: string | null) {
  return iso ? new Date(iso + 'Z').toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—'
}

/** Caixa de confirmação: digitar o nome da família libera o botão perigoso. */
function Confirm({ family, value, onChange }: { family: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={`Digite o nome da família para confirmar: ${family}`}>
      <Input value={value} onChange={(e) => onChange(e.target.value)} autoComplete="off" spellCheck={false} />
    </Field>
  )
}

export function FamilyDataModal({ tenant, onClose, onChanged }:
  { tenant: { id: number; name: string } | null; onClose: () => void; onChanged: () => void }) {
  const { toast } = useSession()
  const id = tenant?.id
  const list = useApi(() => (id ? api.admin.get<{ backups: Backup[] }>(`/admin/tenants/${id}/backups`) : Promise.resolve(null)), [id])
  const [busy, setBusy] = useState('')
  const [mode, setMode] = useState<null | 'wipe' | Backup>(null)
  const [typed, setTyped] = useState('')
  const [months, setMonths] = useState<Window>('1')
  const ok = !!tenant && typed.trim().toLocaleLowerCase() === tenant.name.trim().toLocaleLowerCase()

  async function go(key: string, fn: () => Promise<{ message: string }>) {
    setBusy(key)
    try {
      const r = await fn()
      toast(r.message)
      list.reload(); onChanged(); setMode(null); setTyped('')
    } catch (e) { toast((e as Error).message, 'danger') }
    setBusy('')
  }
  const download = (b: Backup) => api.download(`/admin/tenants/${id}/backups/${b.name}`, {}, b.name, true)
    .catch((e) => toast((e as Error).message, 'danger'))

  return (
    <Modal open={!!tenant} onClose={() => { setMode(null); setTyped(''); onClose() }} wide
      title={`Dados · ${tenant?.name ?? ''}`}>
      {!tenant ? null : mode === 'wipe' ? (
        <div className="stack">
          <p><b>Zerar despesas</b>: apaga todas as despesas da família desde o início (com parcelas e recorrências)
            e os lançamentos já importados do banco. Rendas, cartões, investimentos, metas, pessoas e bancos
            conectados ficam. Um backup completo é feito antes, automaticamente.</p>
          <Field label="Depois de zerar, os bancos conectados trazem de novo">
            <Segmented ariaLabel="Período a reimportar" value={months} onChange={setMonths} options={WINDOWS} />
          </Field>
          <p className="muted small">O que chegar do banco vai para "Revisar importados" (ou é lançado sozinho nos bancos
            com "Lançar automaticamente" ligado).</p>
          <Confirm family={tenant.name} value={typed} onChange={setTyped} />
          <div className="row gap">
            <Button variant="danger" busy={busy === 'wipe'} disabled={!ok}
              onClick={() => go('wipe', () => api.admin.post(`/admin/tenants/${id}/wipe-expenses`,
                { confirm: typed, months: months === '0' ? null : Number(months) }))}>Zerar despesas</Button>
            <Button variant="ghost" onClick={() => { setMode(null); setTyped('') }}>Cancelar</Button>
          </div>
        </div>
      ) : mode ? (
        <div className="stack">
          <p><b>Restaurar o backup de {when(mode.created_at)}</b> ({mode.expenses} despesas): as despesas atuais da família
            são trocadas pelas do backup, exatamente como estavam. O estado de agora é guardado num backup antes.</p>
          <Confirm family={tenant.name} value={typed} onChange={setTyped} />
          <div className="row gap">
            <Button variant="danger" busy={busy === 'restore'} disabled={!ok}
              onClick={() => go('restore', () => api.admin.post(`/admin/tenants/${id}/restore`, { name: mode.name, confirm: typed }))}>
              Restaurar este backup</Button>
            <Button variant="ghost" onClick={() => { setMode(null); setTyped('') }}>Cancelar</Button>
          </div>
        </div>
      ) : (
        <div className="stack">
          <p className="muted">O backup leva tudo da família desde o início (despesas, rendas, cartões, investimentos, metas,
            bancos conectados), sem senhas nem chaves. Fica guardado no servidor e pode ser baixado.</p>
          <div className="row gap">
            <Button icon="cloud-upload" busy={busy === 'backup'}
              onClick={() => go('backup', () => api.admin.post(`/admin/tenants/${id}/backups`))}>Fazer backup agora</Button>
            <Button variant="danger" icon="trash" onClick={() => setMode('wipe')}>Zerar despesas…</Button>
          </div>
          {list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : !list.data ? <Spinner /> : !list.data.backups.length ? (
            <Empty icon="clock-history" title="Nenhum backup ainda" />
          ) : (
            <ul className="backup-list">
              {list.data.backups.map((b) => (
                <li key={b.name}>
                  <div>
                    <b>{when(b.created_at)}</b> <Badge tone={b.reason === 'manual' ? 'info' : 'warning'}>{b.reason ?? 'backup'}</Badge>
                    <div className="muted small">{b.expenses} despesa(s) · {(b.size / 1024).toFixed(0)} KB</div>
                  </div>
                  <div className="row gap">
                    <Button variant="ghost" icon="download" onClick={() => download(b)}>Baixar</Button>
                    <Button variant="ghost" icon="arrow-repeat" onClick={() => setMode(b)}>Restaurar…</Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Modal>
  )
}
