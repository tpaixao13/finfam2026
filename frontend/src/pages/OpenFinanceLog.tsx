// Histórico do Open Finance: o que a Pluggy avisou (webhook) e o que o FinFam leu de cada banco.
// Só dos bancos que a pessoa enxerga; some ao desconectar o banco ou excluir a conta.
import { useState } from 'react'
import { Icon } from '../components/Icon'
import { Empty, ErrorBox, PageHeader, Panel, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Link } from '../lib/router'

interface TxLine { data: string; valor: number; descricao: string; onde?: string; situacao: string; id_pluggy?: string }
interface ReadInfo { lancamentos: TxLine[]; entradas: TxLine[] }   // o que chegou numa leitura do banco
interface Event {
  id: number; at: string; kind: 'webhook' | 'read' | 'error'; title: string; bank_name: string | null
  connection_id: number | null; detail: Record<string, unknown> | ReadInfo | null
}
interface Log { keep_days: number; items: Event[]; banks: { id: number; name: string }[] }

const KIND_ICON = { webhook: 'bell', read: 'arrow-repeat', error: 'exclamation-octagon' } as const
const SITUACAO: Record<string, string> = {
  pending: 'para revisar', imported: 'lançado', duplicate: 'já estava no FinFam', ignored: 'ignorado',
}

const day = (iso: string) => new Date(iso).toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit' })
const time = (iso: string) => new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
const shortDate = (iso: string) => iso.slice(8, 10) + '/' + iso.slice(5, 7)

function byDay(items: Event[]): [string, Event[]][] {
  const out = new Map<string, Event[]>()
  for (const e of items) {
    const label = day(e.at)
    out.set(label, [...(out.get(label) ?? []), e])
  }
  return [...out]
}

function ReadDetail({ detail }: { detail: ReadInfo }) {
  const rows = [...detail.lancamentos.map((t) => ({ ...t, sinal: '−' })), ...detail.entradas.map((t) => ({ ...t, sinal: '+', onde: 'entrada' }))]
  return (
    <ul className="log-tx">
      {rows.map((t, i) => (
        <li key={t.id_pluggy ?? i}>
          <span className="muted">{shortDate(t.data)}</span>
          <span className="log-tx-desc">{t.descricao}</span>
          <span className="muted small">{t.onde} · {SITUACAO[t.situacao] ?? t.situacao}</span>
          <span className={t.sinal === '+' ? 'log-in' : ''}>{t.sinal} {brl(t.valor)}</span>
        </li>
      ))}
    </ul>
  )
}

function Row({ e }: { e: Event }) {
  const read = e.kind === 'read' && e.detail && 'lancamentos' in e.detail
  return (
    <li className={`log-row log-${e.kind}`}>
      <span className="log-icon" aria-hidden><Icon name={KIND_ICON[e.kind]} /></span>
      <div className="log-main">
        <div className="log-head">
          <time dateTime={e.at}>{time(e.at)}</time>
          {e.bank_name && <b>{e.bank_name}</b>}
          <span>{e.title}</span>
        </div>
        {e.detail && (
          <details>
            <summary className="small">{read ? 'Ver o que chegou' : 'Ver o conteúdo do aviso'}</summary>
            {read ? <ReadDetail detail={e.detail as ReadInfo} />
              : <pre className="log-json">{JSON.stringify(e.detail, null, 2)}</pre>}
          </details>
        )}
      </div>
    </li>
  )
}

export function OpenFinanceLogPage() {
  const [bank, setBank] = useState('')
  const data = useApi(() => api.get<Log>(`/pluggy/events${bank ? `?connection_id=${bank}` : ''}`), [bank])
  const log = data.data

  return (
    <div className="page">
      <PageHeader title="Histórico do Open Finance"
        subtitle={<>Os avisos que a Pluggy mandou e o que o FinFam leu de cada banco{log ? `, dos últimos ${log.keep_days} dias` : ''}.
          Some ao desconectar o banco ou excluir a conta.</>}
        actions={<Link to="/bank" className="btn btn-ghost"><Icon name="arrow-left" /><span>Contas conectadas</span></Link>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !log ? <Spinner /> : (
        <Panel title="Eventos" actions={log.banks.length > 1 ? (
          <Select aria-label="Banco" value={bank} onChange={(e) => setBank(e.target.value)}
            options={[{ value: '', label: 'Todos os bancos' }, ...log.banks.map((b) => ({ value: String(b.id), label: b.name }))]} />
        ) : undefined}>
          {!log.items.length ? (
            <Empty icon="clock-history" title="Nada por aqui ainda">
              Quando a Pluggy avisar de algo ou o FinFam ler um banco, aparece aqui.
            </Empty>
          ) : byDay(log.items).map(([label, events]) => (
            <section key={label} className="log-day">
              <h3>{label}</h3>
              <ul className="log-list">{events.map((e) => <Row key={e.id} e={e} />)}</ul>
            </section>
          ))}
        </Panel>
      )}
    </div>
  )
}
