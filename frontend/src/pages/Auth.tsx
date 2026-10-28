import { useState } from 'react'
import { AppVersion } from '../components/AppVersion'
import { Button, Field, Input } from '../components/ui'
import { api, type ApiError } from '../lib/api'
import { Link, navigate } from '../lib/router'
import { useSession } from '../lib/session'
import { CheckEmail, ResendConfirmation } from './AccountEmail'

interface SessionResponse { access_token: string }
type LoginResponse = SessionResponse | { two_factor_required: true; challenge: string }
type RegisterResponse = SessionResponse | { verify_required: true; email: string; message: string }

export function AuthShell({ title, lead, children, footer }: { title: string; lead: string; children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-side">
        <div className="brand brand-lg"><img src="/logo.svg" alt="" width={40} height={40} /><span>FinFam</span></div>
        <p className="auth-pitch">Tenha controle completo das finanças da sua família, reunindo gastos, receitas, faturas e metas em uma única plataforma.</p>
      </div>
      <div className="auth-main">
        <div className="auth-card">
          <h1>{title}</h1>
          <p className="muted">{lead}</p>
          {children}
          <p className="auth-foot">{footer}</p>
        </div>
        <AppVersion />
      </div>
    </div>
  )
}

export function LoginPage() {
  const { login } = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [challenge, setChallenge] = useState<string | null>(null)  // senha certa, falta o código do app
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [unverified, setUnverified] = useState(false)  // senha certa, falta confirmar o e-mail
  const [busy, setBusy] = useState(false)

  async function attempt(task: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setUnverified(false)
    try { await task() } catch (err) {
      setError((err as Error).message)
      setUnverified((err as ApiError).code === 'email_not_verified')
    } finally { setBusy(false) }
  }

  const finish = async (r: SessionResponse) => { await login(r.access_token); navigate('/', { replace: true }) }

  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault()
    attempt(async () => {
      const r = await api.post<LoginResponse>('/auth/login', { email, password })
      if ('two_factor_required' in r) { setChallenge(r.challenge); setCode('') } else await finish(r)
    })
  }

  const submitCode = (e: React.FormEvent) => {
    e.preventDefault()
    attempt(async () => {
      try {
        await finish(await api.post<SessionResponse>('/auth/login/2fa', { challenge, code }))
      } catch (err) {
        if ((err as ApiError).code === '2fa_locked') setChallenge(null)  // bloqueou: volta para o início
        setCode('')
        throw err
      }
    })
  }

  if (challenge) {
    return (
      <AuthShell title="Verificação em duas etapas" lead="Abra o app autenticador (Google, Microsoft, Authy…) e digite o código de 6 dígitos do FinFam."
        footer={<button type="button" className="link-button" onClick={() => { setChallenge(null); setError(null) }}>Voltar e usar outra conta</button>}>
        <form className="stack" onSubmit={submitCode}>
          <Field label="Código do app">
            <Input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} value={code}
              onChange={(e) => setCode(e.target.value)} required autoFocus className="input-code" />
          </Field>
          {error && <p className="form-error" role="alert">{error}</p>}
          <Button type="submit" busy={busy} className="btn-block">Confirmar</Button>
        </form>
      </AuthShell>
    )
  }

  return (
    <AuthShell title="Entrar" lead="Use o e-mail e a senha da sua conta."
      footer={<>Ainda não tem conta? <Link to="/register">Criar conta da família</Link></>}>
      <form className="stack" onSubmit={submitPassword}>
        <Field label="E-mail"><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
        <Field label="Senha"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        {unverified && <ResendConfirmation email={email} />}
        <Button type="submit" busy={busy} className="btn-block">Entrar</Button>
        <Link to="/esqueci-senha" className="auth-forgot">Esqueci minha senha</Link>
      </form>
    </AuthShell>
  )
}

export function RegisterPage() {
  const { login } = useSession()
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm_password: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sentTo, setSentTo] = useState<string | null>(null)  // conta criada: falta confirmar o e-mail
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }))

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (form.password !== form.confirm_password) { setError('As senhas não coincidem.'); return }
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<RegisterResponse>('/auth/register', form)
      if ('verify_required' in r) { setSentTo(r.email); return }
      await login(r.access_token)
      navigate('/', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (sentTo) return <CheckEmail email={sentTo} />

  return (
    <AuthShell title="Criar conta" lead="Você começa com 90 dias de avaliação. Depois pode convidar a família."
      footer={<>Já tem conta? <Link to="/login">Entrar</Link></>}>
      <form className="stack" onSubmit={submit}>
        <Field label="Seu nome"><Input value={form.name} onChange={(e) => set('name', e.target.value)} required minLength={2} maxLength={30} autoFocus /></Field>
        <Field label="E-mail"><Input type="email" autoComplete="email" value={form.email} onChange={(e) => set('email', e.target.value)} required /></Field>
        <Field label="Senha" hint="Mínimo de 8 caracteres"><Input type="password" autoComplete="new-password" value={form.password} onChange={(e) => set('password', e.target.value)} required minLength={8} /></Field>
        <Field label="Confirmar senha"><Input type="password" autoComplete="new-password" value={form.confirm_password} onChange={(e) => set('confirm_password', e.target.value)} required /></Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        <Button type="submit" busy={busy} className="btn-block">Criar conta</Button>
      </form>
    </AuthShell>
  )
}
