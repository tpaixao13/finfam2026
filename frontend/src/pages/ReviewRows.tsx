// Peças das telas de revisão (saídas e entradas): abas, barra de seleção, rascunhos e linhas de saída.
import { useState, type ReactNode } from 'react'
import { Button, Check, IconButton, Input, Money, Segmented, Select } from '../components/ui'
import { brl, dayMonth } from '../lib/format'

export type Status = 'pending' | 'duplicate' | 'ignored' | 'imported'
export interface BankTx {
  id: number; date: string; description: string; amount: number; refund?: boolean; status: Status; reason: string | null
  installment: { number: number; total: number } | null; bank_pending: boolean; bill: string | null
  bank_name: string | null; account_type: 'BANK' | 'CREDIT'; account_name: string | null
  account_last_digits: string | null; card_id: number | null; card_digits: string | null
  expense: { id: number; description: string; amount: number; date: string } | null
  category?: string; payment_method?: string; bank_category?: string | null
  owner_name?: string | null   // banco compartilhado por outra pessoa da família
}
export interface TxList { counts: Record<Status, number>; categories: string[]; items: BankTx[] }
export const REVIEW_STATUSES: Status[] = ['pending', 'duplicate', 'ignored', 'imported']
/** Só o que está ignorado ou repetido volta para a revisão. */
export const canRestore = (tab: Status) => tab === 'duplicate' || tab === 'ignored'

/** Abas por situação, com a contagem de cada uma quando a lista já chegou. */
export function StatusTabs({ tabs, value, counts, onChange }:
  { tabs: Record<Status, string>; value: Status; counts?: Record<Status, number>; onChange: (s: Status) => void }) {
  return (
    <Segmented ariaLabel="Situação" value={value} onChange={onChange}
      options={REVIEW_STATUSES.map((s) => ({ value: s, label: `${tabs[s]}${counts ? ` (${counts[s]})` : ''}` }))} />
  )
}

/** Barra da lista pendente: marcar todos, "N de M · total" e as ações (filhos). */
export function ReviewBar({ picked, count, total, onToggleAll, children }:
  { picked: number; count: number; total: number; onToggleAll: (v: boolean) => void; children: ReactNode }) {
  return (
    <div className="review-bar">
      <Check checked={picked === count} label={`${picked} de ${count} · ${brl(total)}`} onChange={onToggleAll} />
      <div className="row gap">{children}</div>
    </div>
  )
}

/** Rascunhos por id (um por linha pendente): editar uma linha e marcar/desmarcar todas. */
export function useDrafts<D extends { selected: boolean }>() {
  const [drafts, setDrafts] = useState<Record<number, D>>({})
  const edit = (id: number, d: Partial<D>) => setDrafts((s) => ({ ...s, [id]: { ...s[id], ...d } }))
  const selectAll = (selected: boolean) =>
    setDrafts((s) => Object.fromEntries(Object.entries(s).map(([k, d]) => [k, { ...d, selected }])))
  return { drafts, setDrafts, edit, selectAll }
}

export interface Draft { selected: boolean; category: string; description: string; payment_method: string }

/** Linha de origem: data · banco · conta/cartão · parcela. */
function Source({ tx }: { tx: BankTx }) {
  const where = tx.account_type === 'CREDIT' ? 'Cartão' : 'Conta'
  return (
    <span className="muted small">
      {dayMonth(tx.date)} · {tx.bank_name ?? 'Banco'}{tx.owner_name && ` (de ${tx.owner_name})`} · {where}
      {(tx.card_digits || tx.account_last_digits) && ` •••• ${tx.card_digits || tx.account_last_digits}`}
      {tx.installment && ` · parcela ${tx.installment.number}/${tx.installment.total}`}
      {tx.bill && ` · fatura ${tx.bill}`}
      {tx.bank_category && ` · banco: ${tx.bank_category}`}
      {tx.refund && ' · estorno (abate a fatura)'}
      {tx.bank_pending && ' · ainda pendente no banco'}
    </span>
  )
}

const NEW_CATEGORY = '__nova__'

/** Categoria: lista completa (sempre abre inteira) + "Nova categoria…" para digitar uma. */
function CategoryField({ value, categories, label, onChange }:
  { value: string; categories: string[]; label: string; onChange: (v: string) => void }) {
  const [typing, setTyping] = useState(false)
  const [text, setText] = useState('')
  const options = !value || categories.includes(value) ? categories : [...categories, value]
  if (typing) {
    const done = () => { if (text.trim()) onChange(text.trim().slice(0, 50)); setTyping(false) }
    return (
      <div className="cat-new">
        <Input autoFocus aria-label={`Nova categoria para ${label}`} placeholder="Nova categoria" maxLength={50} value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); done() } else if (e.key === 'Escape') setTyping(false) }} />
        <IconButton icon="check2" label="Usar esta categoria" onClick={done} />
        <IconButton icon="x" label="Cancelar" onClick={() => setTyping(false)} />
      </div>
    )
  }
  return (
    <Select aria-label={`Categoria de ${label}`} value={value}
      options={[...options.map((c) => ({ value: c, label: c })), { value: NEW_CATEGORY, label: '+ Nova categoria…' }]}
      onChange={(e) => {
        if (e.target.value === NEW_CATEGORY) { setText(''); setTyping(true) } else onChange(e.target.value)
      }} />
  )
}

export function PendingRow({ tx, draft, categories, onChange }:
  { tx: BankTx; draft: Draft; categories: string[]; onChange: (d: Partial<Draft>) => void }) {
  const credit = tx.account_type === 'CREDIT'
  const parcelado = credit && tx.installment && tx.installment.number <= 1
  return (
    <li className={`review-row ${draft.selected ? 'on' : ''}`}>
      <input type="checkbox" className="review-pick" checked={draft.selected} aria-label={`Selecionar ${tx.description}`}
        onChange={(e) => onChange({ selected: e.target.checked })} />
      <div className="review-main">
        <Input value={draft.description} aria-label={`Descrição de ${tx.description}`} maxLength={200}
          onChange={(e) => onChange({ description: e.target.value })} />
        <Source tx={tx} />
        {parcelado && <span className="small">Vai lançar as {tx.installment!.total} parcelas, como no site.</span>}
        {credit && !tx.card_id && <span className="small warn-text">Cartão do banco não ligado: vai sem cartão do FinFam.</span>}
      </div>
      <div className="review-fields">
        <CategoryField value={draft.category} categories={categories} label={tx.description} onChange={(category) => onChange({ category })} />
        {credit ? <span className="muted small review-method">Cartão de Crédito</span> : (
          <Select aria-label={`Forma de pagamento de ${tx.description}`} value={draft.payment_method} options={['PIX', 'Cartão de Débito']}
            onChange={(e) => onChange({ payment_method: e.target.value })} />
        )}
      </div>
      <Money value={tx.refund ? -tx.amount : tx.amount} className="review-amount" />
    </li>
  )
}

export function DoneRow({ tx, onRestore }: { tx: BankTx; onRestore?: () => void }) {
  return (
    <li className="review-row done">
      <div className="review-main">
        <span>{tx.description}</span>
        <Source tx={tx} />
        {tx.status === 'duplicate' && tx.expense && (
          <span className="small">Igual a <b>{tx.expense.description}</b> de {dayMonth(tx.expense.date)} ({brl(tx.expense.amount)})</span>
        )}
        {tx.status === 'ignored' && tx.reason && <span className="small">{tx.reason}</span>}
      </div>
      <Money value={tx.refund ? -tx.amount : tx.amount} className="review-amount" />
      {onRestore && <Button variant="ghost" aria-label={`Revisar ${tx.description} de novo`} onClick={onRestore}>Revisar</Button>}
    </li>
  )
}
