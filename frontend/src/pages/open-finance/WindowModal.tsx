import { useState } from 'react'
import { Button, Field, Input, Modal, Segmented } from '../../components/ui'

type Months = '1' | '3' | '6' | '12'
const WINDOWS: { value: Months; label: string }[] = [
  { value: '1', label: '1 mês' }, { value: '3', label: '3 meses' }, { value: '6', label: '6 meses' }, { value: '12', label: '12 meses' },
]

/** Antes de abrir a Pluggy: quanto do passado trazer. Com `withId`, registra uma conexão
 *  já feita no painel da Pluggy (cola o ID dela) em vez de abrir a janela. */
export function WindowModal({ open, withId, onClose, onGo }:
  { open: boolean; withId: boolean; onClose: () => void; onGo: (m: number, itemId?: string) => void }) {
  const [months, setMonths] = useState<Months>('1')
  const [itemId, setItemId] = useState('')
  const okId = /^[0-9a-fA-F-]{36}$/.test(itemId.trim())
  return (
    <Modal open={open} title={withId ? 'Usar conexão do painel da Pluggy' : 'Conectar banco'} onClose={onClose} footer={<>
      <Button variant="ghost" onClick={onClose}>Cancelar</Button>
      {withId
        ? <Button icon="check2" disabled={!okId} onClick={() => onGo(Number(months), itemId.trim())}>Usar esta conexão</Button>
        : <Button icon="bank" onClick={() => onGo(Number(months))}>Escolher o banco</Button>}
    </>}>
      <div className="stack">
        {withId && (
          <Field label="ID da conexão (item ID)" hint="No painel da Pluggy, abra a conexão: é o código acima do nome do banco.">
            <Input value={itemId} onChange={(e) => setItemId(e.target.value)} placeholder="add16b55-70fb-4d2d-…"
              autoComplete="off" spellCheck={false} />
          </Field>
        )}
        <div className="field">
          <span className="field-label">Quanto do passado trazer deste banco?</span>
          <Segmented ariaLabel="Período a trazer" value={months} onChange={setMonths} options={WINDOWS} />
        </div>
        <p className="muted small">
          Os lançamentos desse período chegam em <b>Revisar importados</b>. Nada vira despesa sem você aprovar,
          e o que você já lançou à mão ou pelo bot é reconhecido para não duplicar.
        </p>
      </div>
    </Modal>
  )
}
