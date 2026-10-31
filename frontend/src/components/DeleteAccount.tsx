// Excluir a conta (LGPD): a própria pessoa, ou quem criou a família exclui a família inteira.
// Pede senha, código do app (se houver) e a palavra EXCLUIR. Não dá para desfazer.
import { useState } from 'react'
import { api } from '../lib/api'
import { navigate } from '../lib/router'
import { useSession } from '../lib/session'
import { Button, Field, Input, Modal, Panel } from './ui'

type Scope = 'me' | 'family'

export function DeleteAccountPanel({ isOwner, members }: { isOwner: boolean; members: number }) {
  const { me, logout, toast } = useSession()
  const [scope, setScope] = useState<Scope | null>(null)
  const [f, setF] = useState({ password: '', code: '', confirm: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!me) return null
  const twoFactor = me.user.two_factor_enabled
  const others = members > 1
  const close = () => { setScope(null); setF({ password: '', code: '', confirm: '' }); setError(null) }
  const submit = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.post<{ message: string }>('/auth/delete-account', { ...f, code: f.code || null, scope })
      logout()
      navigate('/login', { replace: true })
      toast(r.message)
    } catch (e) { setError((e as Error).message) }
    setBusy(false)
  }
  const ready = f.password && f.confirm.trim().toUpperCase() === 'EXCLUIR' && (!twoFactor || f.code.length >= 6)

  return (
    <Panel title="Excluir conta">
      <div className="stack-sm">
        {!isOwner && <p className="muted">Apaga a sua conta e tudo o que é seu: os gastos, rendas, cartões, metas e
          investimentos que você lançou, os bancos que você conectou (desconectados na Pluggy) e o Telegram. A família
          continua, sem os seus dados.</p>}
        {isOwner && !others && <p className="muted">Você é a única pessoa da família: excluir a conta apaga a família
          e todos os dados dela, e desconecta os bancos na Pluggy.</p>}
        {isOwner && others && <p className="muted">Você criou a família. Para sair, remova antes as outras pessoas em
          {' '}<b>Família</b> — ou exclua a <b>família inteira</b>: todas as {members} pessoas e todos os dados.</p>}
        <p className="muted small">Não dá para desfazer. Quer guardar algo? Exporte as despesas (Excel/PDF) em Despesas antes.
          Se você deixou a família usar a sua chave Pluggy, os bancos que ela conectou com essa chave também são desconectados.</p>
        <div>
          {isOwner && others
            ? <Button variant="danger" onClick={() => setScope('family')}>Excluir a família inteira</Button>
            : <Button variant="danger" onClick={() => setScope('me')}>Excluir minha conta</Button>}
        </div>
      </div>
      <Modal open={scope !== null} onClose={close}
        title={scope === 'family' ? 'Excluir a família inteira' : 'Excluir minha conta'}
        footer={<>
          <Button variant="ghost" onClick={close}>Cancelar</Button>
          <Button variant="danger" busy={busy} disabled={!ready} onClick={submit}>Excluir para sempre</Button>
        </>}>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (ready) submit() }}>
          <p>{scope === 'family'
            ? `Isso apaga a família, as ${members} pessoas e todos os dados. Ninguém conseguirá mais entrar.`
            : 'Isso apaga a sua conta e os seus dados. Não dá para desfazer.'}</p>
          <Field label="Sua senha"><Input type="password" autoComplete="current-password" value={f.password}
            onChange={(e) => setF({ ...f, password: e.target.value })} required /></Field>
          {twoFactor && <Field label="Código do app autenticador"><Input inputMode="numeric" autoComplete="one-time-code"
            value={f.code} maxLength={10} onChange={(e) => setF({ ...f, code: e.target.value.trim() })} required /></Field>}
          <Field label='Para confirmar, digite EXCLUIR'><Input value={f.confirm} autoComplete="off"
            onChange={(e) => setF({ ...f, confirm: e.target.value })} required /></Field>
          {error && <p className="form-error" role="alert">{error}</p>}
        </form>
      </Modal>
    </Panel>
  )
}
