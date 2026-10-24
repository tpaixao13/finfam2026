import { useState } from 'react'
import { Button, Field, Input } from '../components/ui'
import { api } from '../lib/api'
import { Link, navigate } from '../lib/router'
import { useSession } from '../lib/session'

interface SessionResponse { access_token: string }

function AuthShell({ title, lead, children, footer }: { title: string; lead: string; children: React.ReactNode; footer: React.ReactNode }) {
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
      </div>
    </div>
  )
}

export function LoginPage() {
  const { login } = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<SessionResponse>('/auth/login', { email, password })
      await login(r.access_token)
      navigate('/', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell title="Entrar" lead="Use o e-mail e a senha da sua conta."
      footer={<>Ainda não tem conta? <Link to="/register">Criar conta da família</Link></>}>
      <form className="stack" onSubmit={submit}>
        <Field label="E-mail"><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
        <Field label="Senha"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        <Button type="submit" busy={busy} className="btn-block">Entrar</Button>
      </form>
    </AuthShell>
  )
}

export function RegisterPage() {
  const { login } = useSession()
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm_password: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }))

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (form.password !== form.confirm_password) { setError('As senhas não coincidem.'); return }
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<SessionResponse>('/auth/register', form)
      await login(r.access_token)
      navigate('/', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

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
