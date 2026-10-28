import { useState } from 'react'
import { Badge, Button, ConfirmButton, Empty, ErrorBox, Field, IconButton, Input, Modal, Money, MonthNav, PageHeader,
  Progress, Select, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { brl, dateBR, parseMoney } from '../lib/format'
import { useApi, useFormState, usePeriod } from '../lib/hooks'
import { useAction, useSession } from '../lib/session'
import type { Goal } from '../lib/types'

const TYPE_HINT: Record<string, string> = {
  saving: 'Progresso = renda recebida menos gastos do mês.',
  spending_limit: 'Teto de gastos no mês (numa categoria ou no total).',
  debt_reduction: 'Soma do que foi pago desde o início da meta.',
}

function GoalForm({ open, editing, onClose, onSaved }: { open: boolean; editing: Goal | null; onClose: () => void; onSaved: () => void }) {
  const { me, meta, toast } = useSession()
  const blank = { title: '', type: 'saving', target_amount: '', category: '', due_date: '', user_id: String(me?.user.id ?? '') }
  const { f, set, busy, error, setError, run } = useFormState(open, () => editing
    ? { title: editing.title, type: editing.type, target_amount: String(editing.target_amount).replace('.', ','),
      category: editing.category ?? '', due_date: editing.due_date ?? '', user_id: String(editing.user_id) }
    : blank)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const target = parseMoney(f.target_amount)
    if (!Number.isFinite(target) || target <= 0) { setError('Informe o valor da meta.'); return }
    const body = { title: f.title, type: f.type, target_amount: target.toFixed(2), category: f.category || null, due_date: f.due_date || null }
    await run(async () => {
      const r = editing ? await api.patch<{ message: string }>(`/goals/${editing.id}`, body)
        : await api.post<{ message: string }>('/goals', { ...body, user_id: Number(f.user_id) })
      toast(r.message)
      onSaved()
      onClose()
    })
  }

  return (
    <Modal open={open} onClose={onClose} title={editing ? 'Editar meta' : 'Nova meta'}
      footer={<><Button variant="ghost" onClick={onClose}>Cancelar</Button><Button type="submit" form="goal-form" busy={busy}>Salvar meta</Button></>}>
      <form id="goal-form" className="form-grid" onSubmit={submit}>
        <Field label="Nome da meta" className="span-2"><Input value={f.title} onChange={(e) => set('title', e.target.value)} required maxLength={120} placeholder="Ex.: Reserva de emergência" /></Field>
        <Field label="Tipo" hint={TYPE_HINT[f.type]} className="span-2">
          <Select value={f.type} onChange={(e) => set('type', e.target.value)} options={(meta?.goal_types ?? []).map((t) => ({ value: t.id, label: `${t.emoji} ${t.label}` }))} />
        </Field>
        <Field label="Valor (R$)"><Input inputMode="decimal" value={f.target_amount} onChange={(e) => set('target_amount', e.target.value)} required /></Field>
        <Field label="Prazo (opcional)"><Input type="date" value={f.due_date} onChange={(e) => set('due_date', e.target.value)} /></Field>
        {f.type !== 'saving' && (
          <Field label="Categoria (opcional)" className="span-2">
            <Select value={f.category} onChange={(e) => set('category', e.target.value)} placeholder="Todas as categorias" options={meta?.categories ?? []} />
          </Field>
        )}
        {error && <p className="span-2 form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

export function GoalsPage() {
  const { month, year, setPeriod } = usePeriod()
  const { dataVersion } = useSession()
  const act = useAction()
  const data = useApi(() => api.get<{ goals: Goal[] }>('/goals', { month, year }), [month, year, dataVersion])
  const [form, setForm] = useState<{ open: boolean; editing: Goal | null }>({ open: false, editing: null })

  return (
    <div className="page">
      <PageHeader title="Metas" subtitle={<MonthNav month={month} year={year} onChange={setPeriod} />}
        actions={<Button icon="plus" onClick={() => setForm({ open: true, editing: null })}>Nova meta</Button>} />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !data.data ? <Spinner /> : data.data.goals.length === 0 ? (
        <Empty icon="trophy" title="Nenhuma meta ativa.">
          <p>Defina um objetivo — guardar dinheiro, limitar gastos ou quitar uma dívida — e acompanhe o progresso.</p>
          <Button icon="plus" onClick={() => setForm({ open: true, editing: null })}>Criar meta</Button>
        </Empty>
      ) : (
        <div className="goal-grid">
          {data.data.goals.map((g) => {
            const tone = g.status_color === 'danger' ? 'danger' : g.status_color === 'warning' ? 'warning' : 'success'
            return (
              <article key={g.id} className="goal-card">
                <header>
                  <span className="goal-emoji" aria-hidden>{g.emoji}</span>
                  <div><h2>{g.title}</h2><small className="muted">{g.type_label}{g.category && <span className="nowrap"> · {g.category}</span>}</small></div>
                  <Badge tone={tone}>{g.status_label}</Badge>
                </header>
                <div className="goal-amounts">
                  <Money value={g.current} className="goal-current" />
                  <span className="muted">de {brl(g.target_amount)}</span>
                </div>
                <Progress value={g.type === 'spending_limit' ? g.pct_used : g.pct} tone={tone} />
                <p className="goal-coach">{g.coaching}</p>
                <footer>
                  <small className="muted">{g.due_date ? `Prazo ${dateBR(g.due_date)}${g.days_remaining !== null ? ` (${g.days_remaining} dias)` : ''}` : 'Sem prazo'}</small>
                  <span>
                    {!g.completed_at && <IconButton icon="check2-circle" label="Marcar como concluída" onClick={() => act(() => api.post<{ message: string }>(`/goals/${g.id}/complete`), data.reload)} />}
                    <IconButton icon="pencil" label="Editar" onClick={() => setForm({ open: true, editing: g })} />
                    <ConfirmButton label="Arquivar" confirmLabel="Arquivar" icon="x-circle" onConfirm={() => act(() => api.del<{ message: string }>(`/goals/${g.id}`), data.reload)} />
                  </span>
                </footer>
              </article>
            )
          })}
        </div>
      )}
      <GoalForm open={form.open} editing={form.editing} onSaved={data.reload} onClose={() => setForm({ open: false, editing: null })} />
    </div>
  )
}
