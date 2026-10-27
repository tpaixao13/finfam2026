// Alertas, importação de extratos e regras de categoria.
import { useState } from 'react'
import { Icon } from '../components/Icon'
import { Badge, Button, ConfirmButton, Empty, ErrorBox, Field, IconButton, Input, Money, MonthNav, PageHeader,
  Panel, Select, Spinner, Table } from '../components/ui'
import { api } from '../lib/api'
import { brl } from '../lib/format'
import { useApi, usePeriod } from '../lib/hooks'
import { Link } from '../lib/router'
import { useAction, useSession } from '../lib/session'
import type { Alert } from '../lib/types'

export function AlertsPage() {
  const { month, year, setPeriod } = usePeriod()
  const { dataVersion } = useSession()
  const data = useApi(() => api.get<{ alerts: Alert[]; danger: number; warning: number }>('/alerts', { month, year }), [month, year, dataVersion])
  const d = data.data
  return (
    <div className="page">
      <PageHeader title="Alertas" subtitle={<MonthNav month={month} year={year} onChange={setPeriod} />} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : d.alerts.length === 0 ? (
        <Empty icon="shield-check" title="Nenhum alerta. Está tudo em dia." />
      ) : (
        <div className="alerts">
          {d.alerts.map((a) => (
            <div key={a.key} className={`alert alert-${a.type}`}>
              <span className="alert-icon"><Icon name={a.icon} size={20} /></span>
              <div><b>{a.title}</b><p>{a.message}</p></div>
              {a.action_url && <Link to={a.action_url} className="btn btn-ghost">{a.action_label ?? 'Abrir'}</Link>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface PreviewItem {
  index: number; user_id: number; day: number; month: number; year: number; description: string; amount: number
  payment_method: string; bank: string | null; category: string; category_source: string | null; duplicate: boolean
}
interface Row extends PreviewItem { include: boolean; create_rule: boolean }

export function ImportPage() {
  const { me, meta, toast, bumpData } = useSession()
  const formats = useApi(() => api.get<{ id: string; label: string }[]>('/imports/formats'), [])
  const [fmt, setFmt] = useState('ofx')
  const [userId, setUserId] = useState(String(me?.user.id ?? ''))
  const [bank, setBank] = useState('')
  const [password, setPassword] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [rows, setRows] = useState<Row[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [xlsx, setXlsx] = useState<File | null>(null)

  async function parse(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    const fd = new FormData()
    fd.append('file', file)
    fd.append('format', fmt)
    fd.append('user_id', userId)
    if (bank) fd.append('bank', bank)
    if (password) fd.append('pdf_password', password)
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<{ items: PreviewItem[] }>('/imports/preview', fd)
      setRows(r.items.map((i) => ({ ...i, include: !i.duplicate, create_rule: false })))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!rows) return
    const items = rows.filter((r) => r.include).map(({ include: _i, index: _x, category_source: _s, duplicate: _d, ...r }) => ({ ...r, amount: r.amount.toFixed(2) }))
    if (items.length === 0) { toast('Selecione ao menos uma transação.', 'warning'); return }
    setBusy(true)
    try {
      const r = await api.post<{ message: string }>('/imports/confirm', { items })
      toast(r.message)
      setRows(null)
      setFile(null)
      bumpData()
    } catch (err) {
      toast((err as Error).message, 'danger')
    } finally {
      setBusy(false)
    }
  }

  async function importXlsx() {
    if (!xlsx) return
    const fd = new FormData()
    fd.append('file', xlsx)
    try {
      const r = await api.post<{ message: string; imported: number }>('/imports/finfam-xlsx', fd)
      toast(r.message, r.imported ? 'success' : 'warning')
      setXlsx(null)
      bumpData()
    } catch (err) {
      toast((err as Error).message, 'danger')
    }
  }

  const update = (idx: number, patch: Partial<Row>) => setRows((rs) => rs && rs.map((r) => (r.index === idx ? { ...r, ...patch } : r)))
  const selectedTotal = rows?.filter((r) => r.include).reduce((a, r) => a + r.amount, 0) ?? 0

  if (rows) {
    return (
      <div className="page">
        <PageHeader title="Revisar importação" subtitle={<>{rows.filter((r) => r.include).length} de {rows.length} selecionadas · {brl(selectedTotal)}</>}
          actions={<><Button variant="ghost" onClick={() => setRows(null)}>Voltar</Button><Button icon="check2" busy={busy} onClick={confirm}>Importar selecionadas</Button></>} />
        <Panel flush>
          <Table className="table-edit">
              <thead><tr><th /><th>Data</th><th>Descrição</th><th className="num">Valor</th><th>Categoria</th><th>Pagamento</th><th>Criar regra</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.index} className={r.include ? '' : 'row-off'}>
                    <td><input type="checkbox" aria-label="Importar" checked={r.include} onChange={(e) => update(r.index, { include: e.target.checked })} /></td>
                    <td>{String(r.day).padStart(2, '0')}/{String(r.month).padStart(2, '0')}/{r.year}</td>
                    <td>
                      <Input value={r.description} onChange={(e) => update(r.index, { description: e.target.value })} aria-label="Descrição" />
                      {r.duplicate && <Badge tone="warning">Possível duplicata</Badge>}
                    </td>
                    <td className="num"><Money value={r.amount} /></td>
                    <td>
                      <Input list="import-cats" value={r.category} onChange={(e) => update(r.index, { category: e.target.value })} aria-label="Categoria" />
                      {r.category_source && <small className="muted block">{r.category_source === 'rule' ? 'por regra' : 'pelo histórico'}</small>}
                    </td>
                    <td><Select value={r.payment_method} onChange={(e) => update(r.index, { payment_method: e.target.value })} options={meta?.payment_methods ?? []} aria-label="Pagamento" /></td>
                    <td><input type="checkbox" aria-label="Criar regra" checked={r.create_rule} onChange={(e) => update(r.index, { create_rule: e.target.checked })} /></td>
                  </tr>))}
              </tbody>
          </Table>
          <datalist id="import-cats">{(meta?.categories ?? []).map((c) => <option key={c} value={c} />)}</datalist>
        </Panel>
      </div>
    )
  }

  return (
    <div className="page">
      <PageHeader title="Importar extrato" subtitle="Só as saídas são importadas. Você revisa tudo antes de gravar." />
      <div className="grid-2">
        <Panel title="Extrato ou fatura do banco">
          <form className="stack" onSubmit={parse}>
            <Field label="Formato">
              <Select value={fmt} onChange={(e) => setFmt(e.target.value)} options={(formats.data ?? []).map((f) => ({ value: f.id, label: f.label }))} />
            </Field>
            <Field label="De quem são as despesas">
              <Select value={userId} onChange={(e) => setUserId(e.target.value)} options={(me?.members ?? []).map((u) => ({ value: String(u.id), label: u.name }))} />
            </Field>
            <Field label="Banco (opcional)"><Select value={bank} onChange={(e) => setBank(e.target.value)} placeholder="Detectar pelo arquivo" options={meta?.banks ?? []} /></Field>
            {fmt === 'c6pdf' && <Field label="Senha do PDF (se houver)"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>}
            <Field label="Arquivo"><input className="input" type="file" accept=".ofx,.csv,.pdf,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required /></Field>
            {error && <p className="form-error" role="alert">{error}</p>}
            <Button type="submit" icon="cloud-upload" busy={busy} disabled={!file}>Ler arquivo</Button>
          </form>
        </Panel>
        <Panel title="Planilha exportada pelo FinFam">
          <div className="stack">
            <p className="muted">Reimporta um .xlsx gerado em Despesas → Excel. As pessoas são identificadas pelo nome.</p>
            <input className="input" type="file" accept=".xlsx" onChange={(e) => setXlsx(e.target.files?.[0] ?? null)} />
            <Button variant="ghost" icon="cloud-upload" disabled={!xlsx} onClick={importXlsx}>Importar planilha</Button>
          </div>
        </Panel>
      </div>
    </div>
  )
}

interface Rule { id: number; keyword: string; category: string; match_count: number }

export function RulesPage() {
  const { meta } = useSession()
  const act = useAction()
  const rules = useApi(() => api.get<Rule[]>('/rules'), [])
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('')
  const [editing, setEditing] = useState<Rule | null>(null)

  const save = (e: React.FormEvent) => {
    e.preventDefault()
    const body = { keyword, category }
    act(() => editing ? api.put<{ message: string }>(`/rules/${editing.id}`, body) : api.post<{ message: string }>('/rules', body),
      () => { setKeyword(''); setCategory(''); setEditing(null); rules.reload() })
  }

  return (
    <div className="page">
      <PageHeader title="Regras de categoria"
        subtitle="Se a descrição contém a palavra, a categoria é sugerida automaticamente — no lançamento, na importação e no Open Finance. Quando você troca a categoria sugerida em Revisar importados, a regra é criada sozinha."
        actions={<Button variant="ghost" icon="lightning" onClick={() => act(() => api.post<{ message: string }>('/rules/learn'), rules.reload)}>Criar pelo histórico</Button>} />
      <Panel>
        <form className="inline-form" onSubmit={save}>
          <Field label="Palavra-chave"><Input value={keyword} onChange={(e) => setKeyword(e.target.value)} required minLength={2} placeholder="Ex.: IFOOD" /></Field>
          <Field label="Categoria">
            <Input list="rule-cats" value={category} onChange={(e) => setCategory(e.target.value)} required />
            <datalist id="rule-cats">{(meta?.categories ?? []).map((c) => <option key={c} value={c} />)}</datalist>
          </Field>
          <Button type="submit" icon={editing ? 'check2' : 'plus'}>{editing ? 'Salvar regra' : 'Criar regra'}</Button>
          {editing && <Button variant="ghost" type="button" onClick={() => { setEditing(null); setKeyword(''); setCategory('') }}>Cancelar</Button>}
        </form>
      </Panel>
      <Panel flush>
        {rules.error ? <ErrorBox message={rules.error} /> : !rules.data ? <Spinner /> : rules.data.length === 0
          ? <Empty icon="tags" title="Nenhuma regra ainda." />
          : (
            <Table>
              <thead><tr><th>Palavra-chave</th><th>Categoria</th><th className="num">Usos</th><th /></tr></thead>
              <tbody>{rules.data.map((r) => (
                <tr key={r.id}>
                  <td><code>{r.keyword}</code></td><td>{r.category}</td><td className="num">{r.match_count}</td>
                  <td className="actions"><span className="row-actions">
                    <IconButton icon="pencil" label={`Editar regra ${r.keyword}`} onClick={() => { setEditing(r); setKeyword(r.keyword); setCategory(r.category) }} />
                    <ConfirmButton label={`Excluir regra ${r.keyword}`} confirmLabel="Excluir" onConfirm={() => act(() => api.del<{ message: string }>(`/rules/${r.id}`), rules.reload)} />
                  </span></td>
                </tr>))}
              </tbody>
            </Table>
          )}
      </Panel>
    </div>
  )
}
