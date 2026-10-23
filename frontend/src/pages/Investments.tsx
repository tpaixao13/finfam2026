import { useState } from 'react'
import { LineChart } from '../components/charts'
import { Button, ConfirmButton, Empty, ErrorBox, Field, Input, Modal, Money, PageHeader, Panel, Select, Spinner, Table } from '../components/ui'
import { api } from '../lib/api'
import { brl, MONTHS, MONTHS_SHORT, parseMoney, pct } from '../lib/format'
import { useApi, useFormState } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import type { Investment } from '../lib/types'

interface InvestmentsData {
  fixed: Investment[]; crypto: Investment[]; fixed_total: number; crypto_total: number
  selic: number; selic_date: string | null; selic_is_live: boolean; cdi: number; rate_suggestions: Record<string, number>
}
interface Projection { labels: string[]; invested: number[]; projected: number[]; projected_net: number[] }

function InvestmentForm({ open, onClose, onSaved, suggestions }:
  { open: boolean; onClose: () => void; onSaved: () => void; suggestions: Record<string, number> }) {
  const { me, meta, toast } = useSession()
  const now = new Date()
  const blank = { user_id: String(me?.user.id ?? ''), investment_type: '', description: '', amount: '', annual_rate: '',
    crypto_coin: '', month: String(now.getMonth() + 1), year: String(now.getFullYear()) }
  const { f, setF, set, busy, error, setError, run } = useFormState(open, () => blank)
  const isCrypto = f.investment_type === 'Criptomoedas'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const amount = parseMoney(f.amount)
    if (!Number.isFinite(amount) || amount <= 0) { setError('Informe um valor maior que zero.'); return }
    await run(async () => {
      const r = await api.post<{ message: string }>('/investments', {
        user_id: Number(f.user_id), investment_type: f.investment_type, description: f.description || null,
        amount: amount.toFixed(2), annual_rate: isCrypto ? '0' : (parseMoney(f.annual_rate) || 0).toFixed(2),
        crypto_coin: isCrypto ? f.crypto_coin : null, month: Number(f.month), year: Number(f.year) })
      toast(r.message)
      onSaved()
      onClose()
    })
  }

  return (
    <Modal open={open} onClose={onClose} title="Novo investimento"
      footer={<><Button variant="ghost" onClick={onClose}>Cancelar</Button><Button type="submit" form="inv-form" busy={busy}>Registrar</Button></>}>
      <form id="inv-form" className="form-grid" onSubmit={submit}>
        <Field label="Tipo" className="span-2">
          <Select value={f.investment_type} required placeholder="Escolha" options={meta?.investment_types ?? []}
            onChange={(e) => { const t = e.target.value; setF((s) => ({ ...s, investment_type: t, annual_rate: suggestions[t] ? String(suggestions[t]).replace('.', ',') : s.annual_rate })) }} />
        </Field>
        {isCrypto && (
          <Field label="Moeda" className="span-2">
            <Select value={f.crypto_coin} required placeholder="Escolha" onChange={(e) => set('crypto_coin', e.target.value)}
              options={(meta?.crypto_coins ?? []).map((c) => ({ value: c.id, label: c.label }))} />
          </Field>
        )}
        <Field label="Valor investido (R$)"><Input inputMode="decimal" value={f.amount} onChange={(e) => set('amount', e.target.value)} required /></Field>
        {!isCrypto && <Field label="Taxa ao ano (%)" hint="Sugerida a partir da Selic atual"><Input inputMode="decimal" value={f.annual_rate} onChange={(e) => set('annual_rate', e.target.value)} /></Field>}
        <Field label="Descrição (opcional)" className={isCrypto ? '' : 'span-2'}><Input value={f.description} onChange={(e) => set('description', e.target.value)} maxLength={200} /></Field>
        <Field label="Pessoa"><Select value={f.user_id} onChange={(e) => set('user_id', e.target.value)} options={(me?.members ?? []).map((u) => ({ value: String(u.id), label: u.name }))} /></Field>
        <Field label="Mês"><Select value={f.month} onChange={(e) => set('month', e.target.value)} options={MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))} /></Field>
        <Field label="Ano"><Input type="number" value={f.year} onChange={(e) => set('year', e.target.value)} /></Field>
        {error && <p className="span-2 form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

export function InvestmentsPage() {
  const { dataVersion } = useSession()
  const act = useAction()
  const data = useApi(() => api.get<InvestmentsData>('/investments'), [dataVersion])
  const proj = useApi(() => api.get<Projection>('/charts/investments'), [dataVersion])
  const [open, setOpen] = useState(false)
  const d = data.data
  const remove = (id: number) => act(() => api.del<{ message: string }>(`/investments/${id}`), () => { data.reload(); proj.reload() })

  return (
    <div className="page">
      <PageHeader title="Investimentos"
        subtitle={d && <>Selic {pct(d.selic, 2)} · CDI {pct(d.cdi, 2)}{d.selic_is_live ? (d.selic_date ? ` (BCB, ${d.selic_date})` : '') : ' (valor de referência — BCB indisponível)'}</>}
        actions={<Button icon="plus" onClick={() => setOpen(true)}>Novo investimento</Button>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : (
        <>
          <dl className="kpis kpis-inline">
            <div><dt>Renda fixa</dt><dd><Money value={d.fixed_total} /></dd></div>
            <div><dt>Cripto (valor aplicado)</dt><dd><Money value={d.crypto_total} /></dd></div>
          </dl>
          {proj.data && proj.data.labels.length > 0 && (
            <Panel title="Projeção da renda fixa — 12 meses">
              <LineChart ariaLabel="Projeção de 12 meses" labels={proj.data.labels} series={[
                { label: 'Aplicado', data: proj.data.invested, color: 'var(--ink-3)' },
                { label: 'Bruto', data: proj.data.projected, color: 'var(--primary)' },
                { label: 'Líquido de IR/IOF', data: proj.data.projected_net, color: 'var(--income)' }]} />
            </Panel>
          )}
          <Panel title="Renda fixa" flush>
            {d.fixed.length === 0 ? <Empty icon="piggy-bank" title="Nenhum investimento de renda fixa." /> : (
              <Table>
                <thead><tr><th>Investimento</th><th>Pessoa</th><th>Aplicado em</th><th className="num">Taxa a.a.</th><th className="num">Valor</th><th className="num">Rende por dia (líq.)</th><th /></tr></thead>
                <tbody>
                  {d.fixed.map((i) => (
                    <tr key={i.id}>
                      <td><b>{i.investment_type}</b>{i.description && <small className="muted block">{i.description}</small>}</td>
                      <td>{i.user_name}</td>
                      <td>{MONTHS_SHORT[i.month - 1]}/{i.year}</td>
                      <td className="num">{pct(i.annual_rate, 2)}</td>
                      <td className="num"><Money value={i.amount} /></td>
                      <td className="num">{i.daily_yield ? brl(i.daily_yield.net) : '—'}{i.daily_yield?.exempt && <small className="muted block">isento</small>}</td>
                      <td><ConfirmButton label="Remover" confirmLabel="Remover" onConfirm={() => remove(i.id)} /></td>
                    </tr>))}
                </tbody>
              </Table>
            )}
          </Panel>
          {d.crypto.length > 0 && (
            <Panel title="Criptomoedas" flush>
              <Table>
                <thead><tr><th>Moeda</th><th>Pessoa</th><th className="num">Preço de compra</th><th className="num">Valor aplicado</th><th /></tr></thead>
                <tbody>{d.crypto.map((i) => (
                  <tr key={i.id}>
                    <td><b>{i.crypto_coin}</b>{i.description && <small className="muted block">{i.description}</small>}</td>
                    <td>{i.user_name}</td>
                    <td className="num">{i.crypto_buy_price ? brl(i.crypto_buy_price) : '—'}</td>
                    <td className="num"><Money value={i.amount} /></td>
                    <td><ConfirmButton label="Remover" confirmLabel="Remover" onConfirm={() => remove(i.id)} /></td>
                  </tr>))}
                </tbody>
              </Table>
            </Panel>
          )}
        </>
      )}
      <InvestmentForm open={open} onClose={() => setOpen(false)} onSaved={() => { data.reload(); proj.reload() }} suggestions={d?.rate_suggestions ?? {}} />
    </div>
  )
}
