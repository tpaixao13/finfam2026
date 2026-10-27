import { useState } from 'react'
import { Panel, Segmented } from '../components/ui'
import { brl, pct } from '../lib/format'
import { allocate, bankClass, manualClass, type Holding } from '../lib/allocation'
import type { Investment } from '../lib/types'
import type { BankInvestments } from './BankInvestments'

const SLICE_COLORS = ['var(--primary)', 'var(--card)', 'var(--income)', 'var(--warning)', 'var(--info)', 'var(--expense)', 'var(--ink-3)']

/** Tudo o que a família tem, numa lista só: carteira (em R$), cripto (aplicado) e bancos (saldo). */
export function holdings(d: { fixed: Investment[]; crypto: Investment[] }, bank: BankInvestments | null): Holding[] {
  return [
    ...d.fixed.map((i) => ({ klass: manualClass(i.investment_type, false), place: 'Lançados no FinFam', value: i.value_brl ?? i.amount })),
    ...d.crypto.map((i) => ({ klass: 'Cripto', place: 'Lançados no FinFam', value: i.amount })),
    ...(bank?.items ?? []).filter((i) => i.currency === 'BRL')
      .map((i) => ({ klass: bankClass(i.type, i.subtype), place: i.bank, value: i.balance })),
  ]
}

/** "Para onde vai o dinheiro": por classe ou instituição, com a fatia de cada uma. */
export function AllocationPanel({ items }: { items: Holding[] }) {
  const [by, setBy] = useState<'klass' | 'place'>('klass')
  const slices = allocate(items, by)
  if (slices.length === 0) return null
  return (
    <Panel title="Distribuição" actions={<Segmented ariaLabel="Agrupar por" value={by} onChange={setBy}
      options={[{ value: 'klass', label: 'Classes' }, { value: 'place', label: 'Instituições' }]} />}>
      <ul className="alloc">
        {slices.map((s, i) => (
          <li key={s.label}>
            <div className="alloc-head">
              <span>{s.label} <span className="muted">({s.count})</span></span>
              <span><span className="muted">{pct(s.share * 100, 1)}</span> <b>{brl(s.value)}</b></span>
            </div>
            <div className="alloc-bar" role="presentation">
              <span style={{ width: `${Math.max(s.share * 100, 0.5)}%`, background: SLICE_COLORS[i % SLICE_COLORS.length] }} />
            </div>
          </li>))}
      </ul>
    </Panel>
  )
}
