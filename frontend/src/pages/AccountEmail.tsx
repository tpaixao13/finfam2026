// Telas de e-mail da conta: confira seu e-mail, confirmar e-mail (link), esqueci / nova senha.
import { useEffect, useRef, useState } from 'react'
import { Button, Field, Input, PasswordInput, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { useTask } from '../lib/hooks'
import { Link, navigate, useSearchParams } from '../lib/router'
import { AuthShell } from './Auth'

type Message = { message: string }

/** Token do link do e-mail: lido uma vez e tirado da barra de endereço (não fica no histórico). */
function useLinkToken(): string {
  const [params] = useSearchParams()
  const [token] = useState(() => params.get('token') ?? '')
  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, '', window.location.pathname)
  }, [])
  return token
}

/** Mensagem de erro de formulário (lida por leitores de tela assim que aparece). */
function FormError({ message }: { message: string | null }) {
  return message ? <p className="form-error" role="alert">{message}</p> : null
}

/** Senha + confirmação (cadastro e nova senha). */
export function PasswordFields({ value, onChange, label = 'Senha', autoFocus = false }:
  { value: { password: string; confirm_password: string }; onChange: (v: { password: string; confirm_password: string }) => void; label?: string; autoFocus?: boolean }) {
  const set = (k: 'password' | 'confirm_password', v: string) => onChange({ ...value, [k]: v })
  return (
    <>
      <Field label={label} hint="Mínimo de 8 caracteres">
        <PasswordInput autoComplete="new-password" value={value.password} minLength={8} required autoFocus={autoFocus}
          onChange={(e) => set('password', e.target.value)} />
      </Field>
      <Field label={`Confirmar ${label.toLowerCase()}`}>
        <PasswordInput autoComplete="new-password" value={value.confirm_password} required
          onChange={(e) => set('confirm_password', e.target.value)} />
      </Field>
    </>
  )
}

export const PASSWORD_MISMATCH = 'As senhas não coincidem.'

/** Botão "Reenviar e-mail de confirmação" (a resposta é a mesma exista ou não a conta). */
export function ResendConfirmation({ email }: { email: string }) {
  const [reply, setReply] = useState<string | null>(null)
  const { busy, error, run } = useTask()
  if (reply) return <p className="form-ok" role="status">{reply}</p>
  return (
    <>
      <FormError message={error} />
      <button type="button" className="link-button" disabled={busy || !email}
        onClick={() => run(async () => setReply((await api.post<Message>('/auth/verify/resend', { email })).message))}>
        {busy ? 'Enviando…' : 'Reenviar e-mail de confirmação'}
      </button>
    </>
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
  const token = useLinkToken()
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return  // confirma uma vez (o modo estrito do React roda o efeito duas)
    started.current = true
    api.post<Message>('/auth/verify', { token })
      .then((r) => setDone(r.message))
      .catch((err) => setError((err as Error).message))
  }, [token])

  return (
    <AuthShell title="Confirmar e-mail" lead={error ? 'Não deu para confirmar.' : done ? 'Pronto!' : 'Confirmando seu e-mail…'}
      footer={error ? <Link to="/login">Ir para o login</Link> : undefined}>
      {error ? <p className="form-error" role="alert">{error}</p>
        : done ? <>
          <p className="form-ok" role="status">{done}</p>
          <Button className="btn-block" onClick={() => navigate('/login', { replace: true })}>Entrar</Button>
        </> : <Spinner label="Confirmando…" />}
    </AuthShell>
  )
}

/** /esqueci-senha — pede o link por e-mail. */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState<string | null>(null)
  const { busy, error, run } = useTask()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    run(async () => setSent((await api.post<Message>('/auth/forgot-password', { email })).message))
  }

  return (
    <AuthShell title="Esqueci minha senha" lead="Digite o e-mail da sua conta. Enviamos um link para criar uma nova senha."
      footer={<>Lembrou? <Link to="/login">Entrar</Link></>}>
      {sent ? <p className="form-ok" role="status">{sent}</p> : (
        <form className="stack" onSubmit={submit}>
          <Field label="E-mail"><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
          <FormError message={error} />
          <Button type="submit" busy={busy} className="btn-block">Enviar link</Button>
        </form>
      )}
    </AuthShell>
  )
}

/** /redefinir-senha?token=… — cria a nova senha. */
export function ResetPasswordPage() {
  const token = useLinkToken()
  const [form, setForm] = useState({ password: '', confirm_password: '' })
  const [done, setDone] = useState<string | null>(null)
  const { busy, error, setError, run } = useTask()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (form.password !== form.confirm_password) { setError(PASSWORD_MISMATCH); return }
    run(async () => setDone((await api.post<Message>('/auth/reset-password', { token, ...form })).message))
  }

  return (
    <AuthShell title="Criar nova senha" lead="Escolha uma senha nova para a sua conta."
      footer={done ? null : <>Link expirou? <Link to="/esqueci-senha">Pedir outro</Link></>}>
      {done ? (
        <>
          <p className="form-ok" role="status">{done}</p>
          <Button className="btn-block" onClick={() => navigate('/login', { replace: true })}>Entrar</Button>
        </>
      ) : (
        <form className="stack" onSubmit={submit}>
          <PasswordFields value={form} onChange={setForm} label="Nova senha" autoFocus />
          <FormError message={error} />
          <Button type="submit" busy={busy} className="btn-block">Salvar nova senha</Button>
          <p className="muted small">Ao trocar a senha, os outros aparelhos, o Telegram e os tokens de API são desconectados.</p>
        </form>
      )}
    </AuthShell>
  )
}
