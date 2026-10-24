// Verificação em duas etapas (TOTP): painel no perfil e aviso para quem ainda não ativou.
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { navigate, useSearchParams } from '../lib/router'
import { useSession } from '../lib/session'
import { Badge, Button, Field, Input, Modal, Panel } from './ui'

interface Setup { secret: string; otpauth_uri: string; qr_svg: string | null }
type Done = { message: string }

/** De onde vêm as rotas: usuário (/auth/2fa) ou administrador (/admin/2fa, com o token de admin). */
export interface TwoFactorClient { base: string; post: <T>(path: string, body?: unknown) => Promise<T> }
const USER_CLIENT: TwoFactorClient = { base: '/auth/2fa', post: (path, body) => api.post(path, body) }

const SNOOZE_DAYS = 7
const snoozeKey = (uid: number) => `finfam.2fa.snooze.${uid}`

function snoozedUntil(uid: number): number {
  try { return Number(localStorage.getItem(snoozeKey(uid)) || 0) } catch { return 0 }
}

/** Chave em grupos de 4 para digitar no app, se não der para ler o QR code. */
const grouped = (secret: string) => secret.match(/.{1,4}/g)?.join(' ') ?? secret

export function CodeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required
      className="input-code" value={value} onChange={(e) => onChange(e.target.value)} />
  )
}

export function EnableModal({ open, onClose, onDone, client = USER_CLIENT }:
  { open: boolean; onClose: () => void; onDone: () => void; client?: TwoFactorClient }) {
  const [setup, setSetup] = useState<Setup | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setSetup(null); setCode(''); setError(null)
    client.post<Setup>(`${client.base}/setup`).then(setSetup).catch((e) => setError((e as Error).message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function confirm(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      await client.post<Done>(`${client.base}/enable`, { code })
      onDone()
    } catch (err) {
      setError((err as Error).message); setCode('')
    } finally { setBusy(false) }
  }

  return (
    <Modal open={open} onClose={onClose} title="Ativar verificação em duas etapas"
      footer={<><Button variant="ghost" onClick={onClose}>Cancelar</Button>
        <Button type="submit" form="tfa-enable" busy={busy} disabled={!setup}>Ativar</Button></>}>
      <form id="tfa-enable" className="stack" onSubmit={confirm}>
        <ol className="plain-list">
          <li>Instale um app autenticador: <b>Google Authenticator</b>, <b>Microsoft Authenticator</b>, Authy, 1Password…</li>
          <li>No app, toque em <b>+</b> e leia o QR code (ou digite a chave).</li>
          <li>Digite abaixo o código de 6 dígitos que aparecer.</li>
        </ol>
        {setup && (
          <div className="tfa-setup">
            {setup.qr_svg && <img src={setup.qr_svg} alt="QR code para o app autenticador" width={200} height={200} />}
            <p className="muted">Chave para digitar no app:<br /><code className="tfa-secret">{grouped(setup.secret)}</code></p>
          </div>
        )}
        <Field label="Código do app"><CodeInput value={code} onChange={setCode} /></Field>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

export function DisableModal({ open, onClose, onDone, client = USER_CLIENT }:
  { open: boolean; onClose: () => void; onDone: () => void; client?: TwoFactorClient }) {
  const [f, setF] = useState({ password: '', code: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) { setF({ password: '', code: '' }); setError(null) } }, [open])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    try { await client.post<Done>(`${client.base}/disable`, f); onDone() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal open={open} onClose={onClose} title="Desativar verificação em duas etapas"
      footer={<><Button variant="ghost" onClick={onClose}>Cancelar</Button>
        <Button type="submit" form="tfa-disable" variant="danger" busy={busy}>Desativar</Button></>}>
      <form id="tfa-disable" className="stack" onSubmit={submit}>
        <p className="muted">Sem a verificação, quem souber sua senha entra na conta. Confirme com a senha e um código do app.</p>
        <Field label="Senha"><Input type="password" autoComplete="current-password" required value={f.password}
          onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <Field label="Código do app"><CodeInput value={f.code} onChange={(code) => setF({ ...f, code })} /></Field>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

/** Painel em "Perfil e acesso". `?2fa=1` na URL abre a ativação direto (vindo do aviso). */
export function TwoFactorPanel() {
  const { me, refreshMe, toast } = useSession()
  const [params, setParams] = useSearchParams()
  const [modal, setModal] = useState<'enable' | 'disable' | null>(params.get('2fa') === '1' ? 'enable' : null)
  const enabled = Boolean(me?.user.two_factor_enabled)
  const close = () => { setModal(null); if (params.get('2fa')) setParams({ '2fa': null }, true) }
  const done = (message: string) => { close(); refreshMe(); toast(message) }

  return (
    <Panel title="Verificação em duas etapas (2FA)">
      <div className="stack">
        <p>{enabled ? <Badge tone="success">Ativa</Badge> : <Badge tone="warning">Desativada</Badge>}</p>
        <p className="muted">
          {enabled
            ? 'Ao entrar, além da senha, o FinFam pede o código do app autenticador. Se perder o celular, peça ao administrador para remover.'
            : 'Além da senha, pede um código que muda a cada 30 segundos no seu celular (Google Authenticator, Microsoft Authenticator…). Recomendado.'}
        </p>
        <p className="muted">Atenção: 3 códigos errados seguidos no login bloqueiam a conta até o administrador liberar.</p>
        <div>{enabled
          ? <Button variant="ghost" onClick={() => setModal('disable')}>Desativar</Button>
          : <Button icon="shield-check" onClick={() => setModal('enable')}>Ativar</Button>}</div>
      </div>
      <EnableModal open={modal === 'enable'} onClose={close} onDone={() => done('Verificação em duas etapas ativada!')} />
      <DisableModal open={modal === 'disable'} onClose={close} onDone={() => done('Verificação em duas etapas desativada.')} />
    </Panel>
  )
}

/** Aviso depois do login para quem ainda não ativou (pode adiar por 7 dias). */
export function TwoFactorBanner() {
  const { me } = useSession()
  const [hidden, setHidden] = useState(false)
  if (!me || me.user.two_factor_enabled || me.via_api_token || hidden || snoozedUntil(me.user.id) > Date.now()) return null
  const later = () => {
    try { localStorage.setItem(snoozeKey(me.user.id), String(Date.now() + SNOOZE_DAYS * 86400000)) } catch { /* modo privado */ }
    setHidden(true)
  }
  return (
    <div className="notice" role="status">
      <span>🔒 <b>Proteja sua conta:</b> ative a verificação em duas etapas com o app autenticador do celular.</span>
      <span className="row gap">
        <Button onClick={() => navigate('/settings?2fa=1')}>Ativar agora</Button>
        <Button variant="ghost" onClick={later}>Depois</Button>
      </span>
    </div>
  )
}
