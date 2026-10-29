import { useState } from 'react'
import { Badge, Button, Field, Input, Select } from '../../components/ui'
import { api } from '../../lib/api'
import { money } from '../../lib/format'
import { useAction } from '../../lib/session'
import type { Card } from '../../lib/types'
import type { BankAccount } from './types'
import { SaveField } from './SaveField'
import { useBusyAction } from './useBusy'


const accLabel = (acc: BankAccount) => acc.name || (acc.type === 'CREDIT' ? 'Cartão de crédito' : 'Conta')

/** Dia de fechamento: o do banco manda; sem ele, o digitado; sem os dois, o FinFam calcula pelas compras. */
function CloseDayField({ acc, onChange }: { acc: BankAccount; onChange: () => void }) {
  const [busy, act] = useBusyAction()
  const saved = acc.manual_close_day ? String(acc.manual_close_day) : ''
  const [day, setDay] = useState(saved)
  if (acc.close_source === 'bank') {
    return <Field label="Dia de fechamento"><span className="bank-setting-value">Dia {acc.close_day}, informado pelo banco</span></Field>
  }
  const save = () => act(() => api.put<{ message: string }>(`/pluggy/accounts/${acc.id}/close-day`, { close_day: day ? Number(day) : null }), onChange)
  const hint = acc.close_source === 'inferred' && acc.close_day
    ? `O banco não informa; o FinFam calculou dia ${acc.close_day} pelas compras. Digite se for outro.`
    : acc.close_source === 'card' && acc.close_day
      ? `Usando o melhor dia de compra do cartão ligado (dia ${acc.close_day}). Digite só se a fatura fechar em outro dia.`
    : acc.close_source === 'manual' ? 'Digitado por você. Vale até o banco passar a informar o dele.'
      : 'O banco não informa. Veja no app do banco o dia em que a fatura fecha.'
  return (
    <SaveField label="Dia de fechamento" hint={hint} dirty={day !== saved} busy={busy} onSave={save}>
      <Input type="number" min={1} max={31} inputMode="numeric" className="input input-day" value={day}
        placeholder={acc.close_day ? String(acc.close_day) : 'dia'} onChange={(e) => setDay(e.target.value)} />
    </SaveField>
  )
}

function AccountSummary({ acc }: { acc: BankAccount }) {
  if (acc.type !== 'CREDIT') return <>Saldo {money(acc.balance, acc.currency)}</>
  return <>
    Fatura atual {money(acc.balance == null ? null : Math.abs(acc.balance), acc.currency)}
    {acc.credit_limit ? <> · limite {money(acc.credit_limit, acc.currency)}</> : null}
    {acc.available_limit != null ? <> · disponível {money(acc.available_limit, acc.currency)}</> : null}
    {acc.close_day && acc.due_day ? <> · fecha dia {acc.close_day}, vence dia {acc.due_day}</>
      : acc.due_day ? <> · vence dia {acc.due_day}</> : null}
  </>
}

/** Resumo de uma conta ou cartão: só leitura, com aviso quando o cartão não está ligado. */
export function AccountRow({ acc, mine = true }: { acc: BankAccount; mine?: boolean }) {
  const credit = acc.type === 'CREDIT'
  return (
    <li className="bank-acc">
      <div className="bank-acc-main">
        <b>{accLabel(acc)}</b>{acc.last_digits && <span className="muted"> •••• {acc.last_digits}</span>}
        <div className="muted small"><AccountSummary acc={acc} /></div>
        {credit && (acc.card_id
          ? <div className="small muted">Ligado ao cartão {acc.card_label ?? 'do FinFam'}{acc.credit_limit ? '; o limite vem do banco' : ''}.</div>
          : <div className="small"><Badge tone="warning">Cartão não ligado</Badge>{' '}
            {mine && <span className="muted">Abra Ajustes para ligar a um cartão do FinFam.</span>}</div>)}
      </div>
    </li>
  )
}

/** Ajustes de um cartão do banco: ligar ao cartão do FinFam (ou criar), dia de fechamento. */
export function AccountSettings({ acc, cards, onChange }: { acc: BankAccount; cards: Card[]; onChange: () => void }) {
  const act = useAction()
  const [busy, createCard] = useBusyAction()
  const label = accLabel(acc)
  return (
    <li className="bank-acc-settings">
      <b>{label}{acc.last_digits && <span className="muted"> •••• {acc.last_digits}</span>}</b>
      {acc.due_day && <span className="muted small">Vence dia {acc.due_day}, pelas faturas do banco.</span>}
      <div className="bank-settings-grid">
        <Field label="Cartão do FinFam" hint={acc.card_id ? 'As compras deste cartão caem na fatura dele.' : 'Ligue para as compras caírem na fatura certa.'}>
          <div className="bank-setting-row">
            <Select aria-label={`Cartão do FinFam para ${label}`} value={acc.card_id ?? ''} placeholder="Escolher cartão…"
              options={cards.map((c) => ({ value: c.id, label: c.label }))}
              onChange={(e) => act(() => api.put<{ message: string }>(`/pluggy/accounts/${acc.id}`,
                { card_id: e.target.value ? Number(e.target.value) : null }), onChange)} />
            {!acc.card_id && acc.last_digits && (
              <Button variant="ghost" icon="plus" busy={busy}
                onClick={() => createCard(() => api.post<{ message: string }>(`/pluggy/accounts/${acc.id}/card`), onChange)}>
                Criar cartão</Button>
            )}
          </div>
        </Field>
        <CloseDayField key={acc.manual_close_day ?? 'x'} acc={acc} onChange={onChange} />
      </div>
    </li>
  )
}
