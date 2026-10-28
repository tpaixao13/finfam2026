import type { FormEvent, ReactNode } from 'react'
import { Button, Field } from '../../components/ui'

/** "Campo + Salvar" dos Ajustes de um banco: o botão só vale quando o valor mudou. */
export function SaveField({ label, hint, dirty, busy, onSave, children }:
  { label: string; hint?: string; dirty: boolean; busy?: boolean; onSave: () => unknown; children: ReactNode }) {
  const submit = (e: FormEvent) => { e.preventDefault(); onSave() }
  return (
    <form className="bank-setting" onSubmit={submit}>
      <Field label={label} hint={hint}>
        <div className="bank-setting-row">
          {children}
          <Button type="submit" variant="ghost" busy={busy} disabled={!dirty}>Salvar</Button>
        </div>
      </Field>
    </form>
  )
}
