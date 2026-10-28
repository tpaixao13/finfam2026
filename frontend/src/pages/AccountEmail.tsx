// Telas de e-mail da conta: confira seu e-mail, confirmar e-mail (link), esqueci / nova senha.
import { useEffect, useRef, useState } from 'react'
import { Button, Field, Input, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { Link, navigate, useSearchParams } from '../lib/router'
import { useSession } from '../lib/session'
import { AuthShell } from './Auth'

type Message = { message: string }

/** Botão "Reenviar e-mail de confirmação" (a resposta é a mesma exista ou não a conta). */
export function ResendConfirmation({ email }: { email: string }) {
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  if (state !== 'idle' && state !== 'busy') return <p className="form-ok" role="status">{state}</p>
  return (
    <button type="button" className="link-button" disabled={state === 'busy' || !email}
      onClick={async () => {
        setState('busy')
        try { setState((await api.post<Message>('/auth/verify/resend', { email })).message) }
        catch (err) { setState((err as Error).message) }
      }}>
      Reenviar e-mail de confirmação
    </button>
  )
}

/** Conta criada: falta clicar no link do e-mail. */
export function CheckEmail({ email }: { email: string }) {
  return (
    <AuthShell title="Confira seu e-mail" lead={`Enviamos um link de confirmação para ${email}.`}
      footer={<>Já confirmou? <Link to="/login">Entrar</Link></>}>
      <p>Abra o e-mail <b>Bem-vindo ao FinFam</b> e toque em <b>Confirmar e-mail</b>. O link vale por 48 horas.</p>
      <p className="muted small">Não chegou? Veja a caixa de spam ou promoções.</p>
      <ResendConfirmation email={email} />
    </AuthShell>
  )
}

/** /confirmar-email?token=… — confirma e já entra. */
export function ConfirmEmailPage() {
  const { login } = useSession()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return  // o link vale uma confirmação: não repete no modo estrito do React
    started.current = true
    api.post<Message & { access_token?: string }>('/auth/verify', { token })
      .then(async (r) => {
        if (r.access_token) { await login(r.access_token); navigate('/', { replace: true }) } else setDone(r.message)
      })
      .catch((err) => setError((err as Error).message))
  }, [token, login])

  return (
    <AuthShell title="Confirmar e-mail" lead={error ? 'Não deu para confirmar.' : done ? 'Pronto!' : 'Confirmando seu e-mail…'}
      footer={<Link to="/login">Ir para o login</Link>}>
      {error ? <p className="form-error" role="alert">{error}</p> : done ? <p className="form-ok" role="status">{done}</p> : <Spinner />}
    </AuthShell>
  )
}

/** /esqueci-senha — pede o link por e-mail. */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try { setSent((await api.post<Message>('/auth/forgot-password', { email })).message) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <AuthShell title="Esqueci minha senha" lead="Digite o e-mail da sua conta. Enviamos um link para criar uma nova senha."
      footer={<>Lembrou? <Link to="/login">Entrar</Link></>}>
      {sent ? <p className="form-ok" role="status">{sent}</p> : (
        <form className="stack" onSubmit={submit}>
          <Field label="E-mail"><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
          {error && <p className="form-error" role="alert">{error}</p>}
          <Button type="submit" busy={busy} className="btn-block">Enviar link</Button>
        </form>
      )}
    </AuthShell>
  )
}

/** /redefinir-senha?token=… — cria a nova senha. */
export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [form, setForm] = useState({ password: '', confirm_password: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (form.password !== form.confirm_password) { setError('As senhas não coincidem.'); return }
    setBusy(true)
    setError(null)
    try { setDone((await api.post<Message>('/auth/reset-password', { token, ...form })).message) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <AuthShell title="Criar nova senha" lead="Escolha uma senha nova para a sua conta."
      footer={done ? <Link to="/login">Entrar</Link> : <>Link expirou? <Link to="/esqueci-senha">Pedir outro</Link></>}>
      {done ? (
        <>
          <p className="form-ok" role="status">{done}</p>
          <Button className="btn-block" onClick={() => navigate('/login', { replace: true })}>Entrar</Button>
        </>
      ) : (
        <form className="stack" onSubmit={submit}>
          <Field label="Nova senha" hint="Mínimo de 8 caracteres">
            <Input type="password" autoComplete="new-password" value={form.password} minLength={8} required autoFocus
              onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
          </Field>
          <Field label="Confirmar nova senha">
            <Input type="password" autoComplete="new-password" value={form.confirm_password} required
              onChange={(e) => setForm((f) => ({ ...f, confirm_password: e.target.value }))} />
          </Field>
          {error && <p className="form-error" role="alert">{error}</p>}
          <Button type="submit" busy={busy} className="btn-block">Salvar nova senha</Button>
          <p className="muted small">Ao trocar a senha, os outros aparelhos, o Telegram e os tokens de API são desconectados.</p>
        </form>
      )}
    </AuthShell>
  )
}
