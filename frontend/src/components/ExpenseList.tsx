// Lista de despesas reutilizada no painel, despesas, contas a pagar e fatura.
import { api } from '../lib/api'
import { dayMonth } from '../lib/format'
import { useAction, useSession } from '../lib/session'
import type { Expense } from '../lib/types'
import { CATEGORY_ICONS, Icon } from './Icon'
import { Badge, ConfirmButton, IconButton, Money } from './ui'

interface Props {
  items: Expense[]
  onChange: () => void
  showPerson?: boolean
  dateField?: 'purchase_date' | 'due_date'
  selectable?: boolean
  selected?: Set<number>
  onToggleSelect?: (id: number) => void
  compact?: boolean
}

const STATUS: Record<Expense['status'], { tone: 'success' | 'warning' | 'danger'; label: string }> = {
  pago: { tone: 'success', label: 'Pago' },
  pendente: { tone: 'warning', label: 'Pendente' },
  vencida: { tone: 'danger', label: 'Vencida' },
}

export function ExpenseList({ items, onChange, showPerson = true, dateField = 'purchase_date', selectable,
  selected, onToggleSelect, compact }: Props) {
  const { openExpense, bumpData } = useSession()
  const act = useAction()
  const refresh = () => { onChange(); bumpData() }

  return (
    <ul className={`tx-list ${compact ? 'tx-compact' : ''}`}>
      {items.map((e) => (
        <li key={e.id} className={`tx tx-${e.status}`}>
          {selectable && (
            <input type="checkbox" className="tx-check" aria-label={`Selecionar ${e.description}`}
              checked={selected?.has(e.id) ?? false} onChange={() => onToggleSelect?.(e.id)} />
          )}
          <span className="tx-icon"><Icon name={CATEGORY_ICONS[e.category] ?? 'tag'} /></span>
          <div className="tx-main">
            <span className="tx-desc">
              {e.description}
              {e.series && <span className="tx-series">{e.series.type === 'parcelado' ? e.series.label : `↻ ${e.series.label}`}</span>}
            </span>
            <span className="tx-meta">
              {e.category} · {e.card_label ?? e.payment_method}{showPerson && e.user_name ? ` · ${e.user_name}` : ''}
            </span>
          </div>
          <span className="tx-date" title={dateField === 'due_date' ? 'Vencimento' : 'Data da compra'}>
            {dayMonth(e[dateField])}
          </span>
          <Money value={e.amount} className="tx-amount" />
          <button className="tx-status" onClick={() => act(() => api.post<{ message: string }>(`/expenses/${e.id}/toggle-paid`), refresh)}
            title={e.paid ? 'Marcar como pendente' : 'Marcar como pago'}
            aria-label={`${STATUS[e.status].label} — ${e.paid ? 'marcar como pendente' : 'marcar como pago'}`}>
            <Badge tone={STATUS[e.status].tone}>{STATUS[e.status].label}</Badge>
          </button>
          {!compact && (
            <span className="tx-actions">
              <IconButton icon="pencil" label={`Editar ${e.description}`} onClick={() => openExpense(e)} />
              <ConfirmButton label={`Excluir ${e.description}`} confirmLabel="Excluir"
                onConfirm={() => act(() => api.del<{ message: string }>(`/expenses/${e.id}`), refresh)} />
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}
