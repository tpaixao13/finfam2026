import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes,
  type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { brl, initials, MONTHS, shiftMonth } from '../lib/format'
import { Icon } from './Icon'

type Variant = 'primary' | 'ghost' | 'danger' | 'subtle' | 'success'

export function Button({ variant = 'primary', icon, busy, children, className = '', ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; icon?: string; busy?: boolean }) {
  return (
    <button className={`btn btn-${variant} ${className}`} disabled={busy || rest.disabled} {...rest}>
      {busy ? <span className="spinner" aria-hidden /> : icon ? <Icon name={icon} /> : null}
      {children && <span>{children}</span>}
    </button>
  )
}

export function IconButton({ icon, label, variant = 'subtle', className = '', ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; label: string; variant?: Variant }) {
  return (
    <button type="button" className={`icon-btn icon-btn-${variant} ${className}`} aria-label={label} title={label} {...rest}>
      <Icon name={icon} />
    </button>
  )
}

/** Botão que pede confirmação no segundo clique (sem diálogos do navegador). */
export function ConfirmButton({ onConfirm, label, confirmLabel = 'Confirmar', icon = 'trash', text }:
  { onConfirm: () => void; label: string; confirmLabel?: string; icon?: string; text?: string }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(t)
  }, [armed])
  if (armed) {
    return <button className="btn btn-danger btn-sm" onClick={() => { setArmed(false); onConfirm() }}>{confirmLabel}</button>
  }
  if (text) return <Button type="button" variant="ghost" icon={icon} aria-label={label} onClick={() => setArmed(true)}>{text}</Button>
  return <IconButton icon={icon} label={label} onClick={() => setArmed(true)} />
}

export function Field({ label, hint, error, children, className = '' }:
  { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; className?: string }) {
  return (
    <label className={`field ${className}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint && !error && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
  )
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className="input" {...props} />
}

export function Select({ options, placeholder, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & {
  options: (string | { value: string | number; label: string })[]; placeholder?: string }) {
  return (
    <select className="input" {...rest}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => typeof o === 'string'
        ? <option key={o} value={o}>{o}</option>
        : <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

export function Check({ label, checked, onChange, hint }:
  { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  const id = useId()
  return (
    <div className="check">
      <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <label htmlFor={id}>{label}{hint && <small>{hint}</small>}</label>
    </div>
  )
}

export function Segmented<T extends string>({ value, onChange, options, ariaLabel }:
  { value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; ariaLabel: string }) {
  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value}
          className={value === o.value ? 'on' : ''} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  )
}

export function Modal({ open, title, onClose, children, footer, wide }:
  { open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog ref={ref} className={`modal ${wide ? 'modal-wide' : ''}`} onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose() }}>
      {open && (
        <div className="modal-box">
          <header className="modal-head">
            <h2>{title}</h2>
            <IconButton icon="x" label="Fechar" onClick={onClose} />
          </header>
          <div className="modal-body">{children}</div>
          {footer && <footer className="modal-foot">{footer}</footer>}
        </div>
      )}
    </dialog>
  )
}

export function Money({ value, tone, className = '' }: { value: number | null | undefined; tone?: 'auto' | 'expense' | 'income'; className?: string }) {
  const v = value ?? 0
  const cls = tone === 'expense' ? 'neg' : tone === 'income' ? 'pos' : tone === 'auto' ? (v < 0 ? 'neg' : 'pos') : ''
  return <span className={`money ${cls} ${className}`}>{brl(v)}</span>
}

export function Delta({ value, inverse }: { value: number | null | undefined; inverse?: boolean }) {
  if (value === null || value === undefined) return <span className="delta muted">sem comparação</span>
  const good = inverse ? value <= 0 : value >= 0
  return (
    <span className={`delta ${good ? 'good' : 'bad'}`}>
      {value > 0 ? '+' : ''}{value.toFixed(1).replace('.', ',')}% vs mês anterior
    </span>
  )
}

export function Badge({ tone = 'neutral', children }: { tone?: 'neutral' | 'success' | 'danger' | 'warning' | 'info' | 'card'; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function Avatar({ name, url, color, size = 32 }: { name: string; url?: string | null; color?: string; size?: number }) {
  if (url) return <img className="avatar" src={url} alt="" width={size} height={size} />
  return (
    <span className="avatar avatar-initials" style={{ width: size, height: size, background: color ?? 'var(--ink-2)' }}>
      {initials(name)}
    </span>
  )
}

export function Progress({ value, tone = 'primary' }: { value: number; tone?: string }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <span className={`progress-bar tone-${tone}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}

export function Spinner({ label = 'Carregando…' }: { label?: string }) {
  return <div className="loading"><span className="spinner" aria-hidden />{label}</div>
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <Icon name="exclamation-circle" /> <span>{message}</span>
      {onRetry && <Button variant="ghost" onClick={onRetry}>Tentar de novo</Button>}
    </div>
  )
}

export function Empty({ icon = 'receipt', title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={28} />
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  )
}

export function MonthNav({ month, year, onChange }: { month: number; year: number; onChange: (m: number, y: number) => void }) {
  const go = (offset: number) => { const p = shiftMonth(month, year, offset); onChange(p.month, p.year) }
  const now = new Date()
  const isCurrent = month === now.getMonth() + 1 && year === now.getFullYear()
  return (
    <div className="month-nav">
      <IconButton icon="chevron-left" label="Mês anterior" onClick={() => go(-1)} />
      <span className="month-nav-label">{MONTHS[month - 1]} <b>{year}</b></span>
      <IconButton icon="chevron-right" label="Próximo mês" onClick={() => go(1)} />
      {!isCurrent && (
        <button className="link-btn" onClick={() => onChange(now.getMonth() + 1, now.getFullYear())}>Hoje</button>
      )}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  )
}

/** Tabela com rolagem horizontal própria: nunca alarga a página no celular. */
export function Table({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className="table-wrap"><table className={`table ${className}`}>{children}</table></div>
}

export function Panel({ title, actions, children, className = '', flush }:
  { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      <div className={flush ? 'panel-flush' : 'panel-body'}>{children}</div>
    </section>
  )
}
