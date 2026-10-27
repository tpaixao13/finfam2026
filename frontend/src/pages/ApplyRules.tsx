// Regras de categoria → despesas já lançadas: prévia do que muda e confirmação.
import { useEffect, useState } from 'react'
import { Button, Check, Empty, Modal, Spinner, Table } from '../components/ui'
import { api } from '../lib/api'
import { brl, dateBR } from '../lib/format'
import { useAction } from '../lib/session'

interface Change { id: number; description: string; date: string; amount: number; from: string | null; to: string; keyword: string }
interface Preview { count: number; sample: Change[]; message: string }

export function ApplyRulesModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const act = useAction()
  const [onlyOthers, setOnlyOthers] = useState(true)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setPreview(null)
    api.post<Preview>('/rules/apply', { only_others: onlyOthers, dry_run: true }).then(setPreview).catch(() => setPreview(null))
  }, [open, onlyOthers])

  const apply = async () => {
    setBusy(true)
    await act(() => api.post<Preview>('/rules/apply', { only_others: onlyOthers, dry_run: false }), () => { onDone(); onClose() })
    setBusy(false)
  }

  return (
    <Modal open={open} title="Aplicar regras às despesas" onClose={onClose} wide
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        <Button icon="check2" busy={busy} disabled={!preview?.count} onClick={apply}>
          {preview?.count ? `Atualizar ${preview.count} despesa(s)` : 'Atualizar'}
        </Button>
      </>}>
      <Check label='Só as despesas em "Outros"' checked={onlyOthers} onChange={setOnlyOthers}
        hint="Desmarcado, a regra também troca categorias que você escolheu à mão." />
      {!preview ? <Spinner /> : preview.count === 0 ? <Empty icon="tags" title={preview.message} /> : (
        <>
          <p className="small">{preview.message}{preview.count > preview.sample.length && ` Mostrando as ${preview.sample.length} primeiras.`}</p>
          <div className="apply-preview">
            <Table>
              <thead><tr><th>Despesa</th><th className="hide-sm">Data</th><th className="num hide-sm">Valor</th><th>Categoria</th></tr></thead>
              <tbody>{preview.sample.map((c) => (
                <tr key={c.id}>
                  <td>{c.description}<small className="muted block">regra <code>{c.keyword}</code></small></td>
                  <td className="hide-sm nowrap">{dateBR(c.date)}</td>
                  <td className="num hide-sm nowrap">{brl(c.amount)}</td>
                  <td><span className="muted">{c.from || '—'}</span> → <b>{c.to}</b></td>
                </tr>))}
              </tbody>
            </Table>
          </div>
        </>
      )}
    </Modal>
  )
}
