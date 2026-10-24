import { Button, Field, Input, Modal, Select } from '../components/ui'
import { api } from '../lib/api'
import { brl, decimalBR, MONTHS, parseMoney, rate } from '../lib/format'
import { useApi, useFormState } from '../lib/hooks'
import { useSession } from '../lib/session'

export function InvestmentForm({ open, onClose, onSaved, suggestions }:
  { open: boolean; onClose: () => void; onSaved: () => void; suggestions: Record<string, number> }) {
  const { me, meta, toast } = useSession()
  const now = new Date()
  const blank = { user_id: String(me?.user.id ?? ''), investment_type: '', description: '', amount: '', annual_rate: '',
    crypto_coin: '', currency: 'BRL', fx_buy_rate: '', month: String(now.getMonth() + 1), year: String(now.getFullYear()) }
  const { f, setF, set, busy, error, setError, run } = useFormState(open, () => blank)
  const isCrypto = f.investment_type === 'Criptomoedas'
  const currency = isCrypto ? 'BRL' : f.currency
  const foreign = currency !== 'BRL'
  const symbol = meta?.currencies.find((c) => c.code === currency)?.symbol ?? 'R$'
  const fx = useApi(() => (open ? api.get<{ rates: Record<string, number> }>('/market/fx') : Promise.resolve(null)), [open])
  const todayRate = fx.data?.rates[currency]
  // taxa sugerida pela Selic só vale para investimentos em reais
  const suggest = (type: string, cur: string) => (cur === 'BRL' && suggestions[type] ? decimalBR(suggestions[type]) : '')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const amount = parseMoney(f.amount)
    if (!Number.isFinite(amount) || amount <= 0) { setError('Informe um valor maior que zero.'); return }
    await run(async () => {
      const r = await api.post<{ message: string }>('/investments', {
        user_id: Number(f.user_id), investment_type: f.investment_type, description: f.description || null,
        amount: amount.toFixed(2), annual_rate: isCrypto ? '0' : (parseMoney(f.annual_rate) || 0).toFixed(2),
        crypto_coin: isCrypto ? f.crypto_coin : null, currency,
        fx_buy_rate: foreign && f.fx_buy_rate ? parseMoney(f.fx_buy_rate).toFixed(6) : null,
        month: Number(f.month), year: Number(f.year) })
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
            onChange={(e) => { const t = e.target.value; setF((s) => ({ ...s, investment_type: t, annual_rate: suggest(t, s.currency) || s.annual_rate })) }} />
        </Field>
        {isCrypto && (
          <Field label="Criptomoeda" className="span-2">
            <Select value={f.crypto_coin} required placeholder="Escolha" onChange={(e) => set('crypto_coin', e.target.value)}
              options={(meta?.crypto_coins ?? []).map((c) => ({ value: c.id, label: c.label }))} />
          </Field>
        )}
        {!isCrypto && (
          <Field label="Moeda" className={foreign ? '' : 'span-2'}>
            <Select value={f.currency} onChange={(e) => { const c = e.target.value; setF((s) => ({ ...s, currency: c, fx_buy_rate: '', annual_rate: suggest(s.investment_type, c) })) }}
              options={(meta?.currencies ?? [{ code: 'BRL', name: 'Real', symbol: 'R$' }]).map((c) => ({ value: c.code, label: `${c.name} (${c.symbol})` }))} />
          </Field>
        )}
        {foreign && (
          <Field label={`Cotação na compra (R$ por ${symbol})`}
            hint={todayRate ? `Em branco: cotação de hoje (${rate(todayRate)})` : 'Em branco: cotação de hoje'}>
            <Input inputMode="decimal" value={f.fx_buy_rate} onChange={(e) => set('fx_buy_rate', e.target.value)}
              placeholder={todayRate ? todayRate.toFixed(todayRate < 1 ? 4 : 2).replace('.', ',') : ''} />
          </Field>
        )}
        <Field label={`Valor investido (${symbol})`}
          hint={foreign && todayRate && parseMoney(f.amount) > 0 ? `≈ ${brl(parseMoney(f.amount) * (parseMoney(f.fx_buy_rate) || todayRate))}` : undefined}>
          <Input inputMode="decimal" value={f.amount} onChange={(e) => set('amount', e.target.value)} required />
        </Field>
        {!isCrypto && <Field label="Taxa ao ano (%)" hint={foreign ? `Rendimento em ${symbol} (ex.: 4). Em branco: só câmbio.` : 'Sugerida a partir da Selic atual'}><Input inputMode="decimal" value={f.annual_rate} onChange={(e) => set('annual_rate', e.target.value)} /></Field>}
        <Field label="Descrição (opcional)" className={isCrypto ? '' : 'span-2'}><Input value={f.description} onChange={(e) => set('description', e.target.value)} maxLength={200} /></Field>
        <Field label="Pessoa" className="span-2"><Select value={f.user_id} onChange={(e) => set('user_id', e.target.value)} options={(me?.members ?? []).map((u) => ({ value: String(u.id), label: u.name }))} /></Field>
        <Field label="Mês"><Select value={f.month} onChange={(e) => set('month', e.target.value)} options={MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))} /></Field>
        <Field label="Ano"><Input type="number" value={f.year} onChange={(e) => set('year', e.target.value)} /></Field>
        {error && <p className="span-2 form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}
