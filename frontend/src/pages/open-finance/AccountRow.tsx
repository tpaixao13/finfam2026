import { useState } from 'react'
import { Badge, Button, Input, Select } from '../../components/ui'
import { api } from '../../lib/api'
import { money } from '../../lib/format'
import { useAction } from '../../lib/session'
import type { Card } from '../../lib/types'
import type { BankAccount } from './types'
import { useBusy } from './useBusy'

const SOURCE = { bank: 'informado pelo banco', manual: 'digitado por você', inferred: 'calculado pelas compras' } as const

const accLabel = (acc: BankAccount) => acc.name || (acc.type === 'CREDIT' ? 'Cartão de crédito' : 'Conta')

/** Fechamento digitado: vale enquanto o banco não informa o dele (vazio = o FinFam calcula). */
function CloseDayField({ acc, onChange }: { acc: BankAccount; onChange: () => void }) {
  const act = useAction()
  const saved = acc.manual_close_day ? String(acc.manual_close_day) : ''
  const [day, setDay] = useState(saved)
  const [busy, run] = useBusy()
  const save = (e: React.FormEvent) => {
    e.preventDefault()
    return run(() => act(() => api.put<{ message: string }>(`/pluggy/accounts/${acc.id}/close-day`, { close_day: day ? Number(day) : null }), onChange))
  }
  return (
    <form className="bank-close" onSubmit={save} title="O banco não informa o fechamento. O que você digitar vale até ele passar a informar.">
      <Input type="number" min={1} max={31} inputMode="numeric" aria-label="Dia de fechamento da fatura" placeholder="Fecha dia"
        value={day} onChange={(e) => setDay(e.target.value)} />
      {day !== saved && <Button type="submit" variant="ghost" icon="check2" busy={busy}>Salvar</Button>}
    </form>
  )
}

function AccountSummary({ acc }: { acc: BankAccount }) {
  if (acc.type !== 'CREDIT') return <>Saldo {money(acc.balance, acc.currency)}</>
  return <>
    Fatura atual {money(acc.balance == null ? null : Math.abs(acc.balance), acc.currency)}
    {acc.credit_limit ? <> · limite {money(acc.credit_limit, acc.currency)}</> : null}
    {acc.available_limit != null ? <> · disponível {money(acc.available_limit, acc.currency)}</> : null}
    {acc.close_day && acc.due_day ? <> · fecha dia {acc.close_day}, vence dia {acc.due_day}</> : null}
  </>
}

/** Resumo de uma conta ou cartão: só leitura, com aviso quando o cartão não está ligado. */
export function AccountRow({ acc }: { acc: BankAccount }) {
  const credit = acc.type === 'CREDIT'
  return (
    <li className="bank-acc">
      <div className="bank-acc-main">
        <b>{accLabel(acc)}</b>{acc.last_digits && <span className="muted"> •••• {acc.last_digits}</span>}
        <div className="muted small"><AccountSummary acc={acc} /></div>
        {credit && (acc.card_id
          ? <div className="small muted">Ligado ao cartão {acc.card_label ?? 'do FinFam'}{acc.credit_limit ? '; o limite vem do banco' : ''}.</div>
          : <div className="small"><Badge tone="warning">Cartão não ligado</Badge>{' '}
            <span className="muted">Abra Ajustes para ligar a um cartão do FinFam.</span></div>)}
      </div>
    </li>
  )
}

/** Ajustes de um cartão do banco: ligar ao cartão do FinFam, criar cartão, dia de fechamento. */
export function AccountSettings({ acc, cards, onChange }: { acc: BankAccount; cards: Card[]; onChange: () => void }) {
  const act = useAction()
  const [busy, run] = useBusy()
  const label = accLabel(acc)
  return (
    <li className="bank-acc">
      <div className="bank-acc-main">
        <b>{label}</b>{acc.last_digits && <span className="muted"> •••• {acc.last_digits}</span>}
        {acc.close_day && <div className="muted small">Fechamento {SOURCE[acc.close_source ?? 'inferred']}.</div>}
      </div>
      <div className="bank-acc-map">
        <Select aria-label={`Cartão do FinFam para ${label}`} value={acc.card_id ?? ''}
          placeholder="Ligar a um cartão…"
          options={cards.map((c) => ({ value: c.id, label: c.label }))}
          onChange={(e) => act(() => api.put<{ message: string }>(`/pluggy/accounts/${acc.id}`,
            { card_id: e.target.value ? Number(e.target.value) : null }), onChange)} />
        {acc.close_source !== 'bank' && <CloseDayField acc={acc} onChange={onChange} />}
        {!acc.card_id && acc.last_digits && (
          <Button variant="ghost" icon="plus" busy={busy}
            onClick={() => run(() => act(() => api.post<{ message: string }>(`/pluggy/accounts/${acc.id}/card`), onChange))}>
            Criar cartão</Button>
        )}
      </div>
    </li>
  )
}
