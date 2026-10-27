import type { ReactNode } from 'react'
import { Switch } from '../../components/ui'

/** Interruptor com título e explicação ao lado (o título também é o nome acessível). */
export function ToggleRow({ label, title, children, checked, onChange }:
  { label: string; title: string; children: ReactNode; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="bank-auto">
      <Switch label={label} checked={checked} onChange={onChange} />
      <span className="small"><b>{title}</b><span className="muted"> — {children}</span></span>
    </div>
  )
}
