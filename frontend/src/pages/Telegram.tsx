import { useState } from 'react'
import { Badge, Button, Check, ConfirmButton, ErrorBox, Field, Input, PageHeader, Panel, Select, Spinner } from '../components/ui'
import { ApiTokensPanel } from '../components/ApiTokensPanel'
import { api } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/session'

interface TelegramData {
  bot_configured: boolean; bot_username: string | null; linked: boolean; telegram_username: string | null
  members: { id: number; name: string; linked: boolean }[]
  enabled: boolean; hour: number; minute: number; configured: boolean; legacy_configured: boolean
  alerts: boolean
  group: { connected: boolean; title: string | null; can_manage: boolean }
}
interface AiProvider { id: string; label: string; default_model: string; key_url: string }
interface AiData { configured: boolean; enabled: boolean; provider: string | null; model: string | null; base_url: string | null; key_masked: string; providers: AiProvider[]; can_edit: boolean }
type Done = { message: string }

const HOURS = Array.from({ length: 24 }, (_, i) => ({ value: String(i), label: `${String(i).padStart(2, '0')}h` }))
const MINUTES = Array.from({ length: 12 }, (_, i) => ({ value: String(i * 5), label: String(i * 5).padStart(2, '0') }))

export function TelegramPage() {
  const data = useApi(() => api.get<TelegramData>('/settings/telegram'), [])
  const d = data.data
  return (
    <div className="page">
      <PageHeader title="Telegram e IA" subtitle="Consulte contas, pague e lance gastos conversando com o bot do FinFam, ou pelo seu assistente de IA (MCP)." />
      {data.error ? <ErrorBox message={data.error} onRetry={data.reload} /> : !d ? <Spinner /> : (
        <div className="grid-2">
          <div className="stack-lg">
            <TelegramLinkPanel d={d} reload={data.reload} />
            <GroupPanel d={d} reload={data.reload} />
            <ReminderPanel d={d} reload={data.reload} />
            <BotCommandsPanel />
          </div>
          <div className="stack-lg">
            <AiPanel />
            <ApiTokensPanel />
          </div>
        </div>
      )}
    </div>
  )
}

function BotCommandsPanel() {
  return (
    <Panel title="O que o bot faz">
      <ul className="plain-list">
        <li><code>/menu</code> botões com tudo o que o bot faz</li>
        <li><code>/contas</code> contas a pagar, com botão para marcar como paga · <code>/agenda</code> as da semana, dia a dia</li>
        <li><code>/resumo</code> resumo do mês · <code>/semana</code> como foi a semana · <code>/metas</code> progresso das metas</li>
        <li><code>/lancar</code> lançar um gasto passo a passo</li>
        <li><code>/investir</code> registrar um investimento (real, dólar, euro…)</li>
        <li><code>/carteira</code> seus investimentos (lançados e dos bancos)</li>
        <li><code>/banco</code> bancos conectados: o que chegou, saldo e se a fatura bate</li>
        <li><code>/revisar</code> revisar o que chegou do banco ali mesmo: lançar, trocar categoria ou ignorar</li>
        <li>Com a IA ligada: mande a <b>foto do cupom</b> ou um <b>áudio</b> (áudio só com Gemini) e confirme com ✅</li>
        <li><i>gastei 30 de farmácia pra Greyce</i> lança para outra pessoa da família</li>
        <li>Perguntas como <i>quais meus investimentos?</i> ou <i>saldo da conta do C6?</i> respondem mesmo sem IA</li>
        <li>Com a IA ligada, é só escrever: <i>gastei 87,50 no mercado no cartão Inter em 3x</i></li>
      </ul>
      <p className="muted">Nada é lançado sem você confirmar no ✅. Cada pessoa conecta o próprio Telegram e vê só os dados da família dela.</p>
    </Panel>
  )
}

function GroupPanel({ d, reload }: { d: TelegramData; reload: () => void }) {
  const act = useAction()
  const [link, setLink] = useState<{ url: string } | null>(null)
  if (!d.bot_configured) return null
  return (
    <Panel title="Grupo da família">
      <div className="stack">
        {d.group.connected ? (
          <>
            <p><Badge tone="success">Conectado</Badge>{d.group.title && <> ao grupo <b>{d.group.title}</b></>}</p>
            <p className="muted">Os avisos da família (contas do dia, faturas, resumo da semana e metas) chegam só no grupo,
              e não mais no privado de cada pessoa. Os avisos do banco de cada um continuam no privado dele.</p>
            {d.group.can_manage && <div><ConfirmButton label="Desconectar o grupo" text="Desconectar grupo" confirmLabel="Voltar para o privado de cada um"
              icon="trash" onConfirm={() => act(() => api.del<Done>('/telegram/group'), () => { setLink(null); reload() })} /></div>}
          </>
        ) : link ? (
          <>
            <a className="btn btn-primary" href={link.url} target="_blank" rel="noreferrer">Abrir o Telegram e escolher o grupo</a>
            <p className="muted">Escolha o grupo da família e confirme. O link vale 15 minutos e só funciona uma vez. Depois,{' '}
              <button type="button" className="link-button" onClick={reload}>atualize esta página</button>.</p>
          </>
        ) : (
          <>
            <p className="muted">Prefere receber os avisos num grupo do Telegram com a família toda? Conecte o grupo e cada
              pessoa deixa de receber os mesmos avisos no privado. Consultar e lançar continua no privado.</p>
            {d.group.can_manage
              ? <div><Button variant="ghost" icon="telegram"
                  onClick={() => act(() => api.post<{ url: string; message?: string }>('/telegram/group-code'), setLink)}>
                  Conectar um grupo</Button></div>
              : <p className="muted small">Só quem criou a família pode conectar o grupo.</p>}
          </>
        )}
      </div>
    </Panel>
  )
}

function TelegramLinkPanel({ d, reload }: { d: TelegramData; reload: () => void }) {
  const act = useAction()
  const [link, setLink] = useState<{ url: string; expires_at: string } | null>(null)
  if (!d.bot_configured) {
    return (
      <Panel title="Seu Telegram">
        <p className="muted">O bot do FinFam ainda não foi ativado neste servidor. O administrador precisa definir <code>TELEGRAM_BOT_TOKEN</code> e <code>TELEGRAM_BOT_USERNAME</code>.</p>
      </Panel>
    )
  }
  return (
    <Panel title="Seu Telegram">
      <div className="stack">
        {d.linked ? (
          <>
            <p><Badge tone="success">Conectado</Badge>{d.telegram_username && <> como <b>@{d.telegram_username}</b></>}</p>
            <p className="muted">Abra <a href={`https://t.me/${d.bot_username}`} target="_blank" rel="noreferrer">@{d.bot_username}</a> no Telegram e mande <code>/contas</code>.</p>
            <div><ConfirmButton label="Desconectar Telegram" text="Desconectar" confirmLabel="Confirmar desconexão" icon="trash"
              onConfirm={() => act(() => api.del<Done>('/telegram/link'), () => { setLink(null); reload() })} /></div>
          </>
        ) : link ? (
          <>
            <a className="btn btn-primary" href={link.url} target="_blank" rel="noreferrer">Abrir o Telegram e conectar</a>
            <p className="muted">No Telegram, toque em <b>Iniciar</b>. O link vale 15 minutos e só funciona uma vez. Depois, <button type="button" className="link-button" onClick={reload}>atualize esta página</button>.</p>
          </>
        ) : (
          <>
            <p className="muted">Conecte o seu Telegram ao <b>@{d.bot_username}</b> para consultar e lançar pelo celular.</p>
            <div><Button icon="telegram" onClick={() => act(() => api.post<{ url: string; expires_at: string; message?: string }>('/telegram/link-code'), setLink)}>Conectar Telegram</Button></div>
          </>
        )}
        {d.members.length > 1 && (
          <p className="muted">Na família: {d.members.map((m, i) => <span key={m.id}>{i > 0 && ' · '}{m.name} ({m.linked ? 'conectado' : 'não conectado'})</span>)}</p>
        )}
      </div>
    </Panel>
  )
}

function ReminderPanel({ d, reload }: { d: TelegramData; reload: () => void }) {
  const act = useAction()
  const [f, setF] = useState<{ enabled: boolean; hour: string; minute: string; alerts: boolean } | null>(null)
  const form = f ?? { enabled: d.enabled, hour: String(d.hour), minute: String(d.minute), alerts: d.alerts }
  return (
    <Panel title="Lembretes e avisos">
      <form className="stack" onSubmit={(e) => {
        e.preventDefault()
        act(() => api.put<Done>('/settings/telegram', { enabled: form.enabled, hour: Number(form.hour), minute: Number(form.minute), alerts: form.alerts }),
          () => { setF(null); reload() })
      }}>
        <p className="muted">Todo dia, no horário escolhido, quem conectou o Telegram recebe as contas do dia, as atrasadas
          e os alertas da família. Com os avisos automáticos, também a fatura que fecha amanhã ou vence em 2 dias e — só
          para quem conectou o banco — a conta que vence maior que o saldo.</p>
        <Check label="Enviar lembrete todos os dias" checked={form.enabled} onChange={(v) => setF({ ...form, enabled: v })} />
        <div className="row gap">
          <Field label="Hora"><Select value={form.hour} onChange={(e) => setF({ ...form, hour: e.target.value })} options={HOURS} /></Field>
          <Field label="Minuto"><Select value={form.minute} onChange={(e) => setF({ ...form, minute: e.target.value })} options={MINUTES} /></Field>
        </div>
        <Check label="Avisos automáticos: resumo da semana (domingo, 19h), metas de limite em 80% e 100%, faturas e saldo"
          checked={form.alerts} onChange={(v) => setF({ ...form, alerts: v })} />
        {d.legacy_configured && !d.members.some((m) => m.linked) && (
          <p className="muted">Enquanto ninguém conectar o Telegram, o lembrete continua saindo pelo bot próprio configurado no FinFam antigo.</p>
        )}
        <div className="row gap">
          <Button type="submit">Salvar</Button>
          <Button type="button" variant="ghost" icon="telegram" disabled={!d.configured}
            title={d.configured ? undefined : 'Conecte o Telegram primeiro'}
            onClick={() => act(() => api.post<Done>('/settings/telegram/test'))}>Enviar teste agora</Button>
        </div>
      </form>
    </Panel>
  )
}

const AI_TITLE = 'Inteligência artificial (opcional)'

function AiPanel() {
  const act = useAction()
  const data = useApi(() => api.get<AiData>('/settings/ai'), [])
  const [f, setF] = useState<{ provider: string; api_key: string; model: string; base_url: string; enabled: boolean } | null>(null)
  const d = data.data
  if (data.error) return <Panel title={AI_TITLE}><ErrorBox message={data.error} onRetry={data.reload} /></Panel>
  if (!d) return <Panel title={AI_TITLE}><Spinner /></Panel>
  const form = f ?? { provider: d.provider ?? 'gemini', api_key: '', model: d.model ?? '', base_url: d.base_url ?? '', enabled: d.configured ? d.enabled : true }
  const provider = d.providers.find((p) => p.id === form.provider) ?? d.providers[0]
  // a chave atual só é mantida se o destino não mudar (provedor e endereço iguais)
  const sameProvider = d.configured && d.provider === form.provider
    && (form.provider !== 'openai' || (d.base_url ?? '') === form.base_url.trim().replace(/\/+$/, ''))
  const done = () => { setF(null); data.reload() }
  return (
    <Panel title={AI_TITLE}>
      <form className="stack" onSubmit={(e) => {
        e.preventDefault()
        if (!d.can_edit) return
        act(() => api.put<Done>('/settings/ai', { provider: form.provider, api_key: form.api_key || null,
          model: form.model || null, base_url: form.provider === 'openai' ? form.base_url || null : null, enabled: form.enabled }), done)
      }}>
        <p className="muted">Com a IA, o bot entende frases como <i>"paguei 120 de luz ontem no PIX"</i>. A família usa a <b>própria chave</b> e paga direto ao provedor (para lançar gastos, costuma ser centavos por mês).</p>
        <Field label="Provedor">
          <Select value={form.provider} onChange={(e) => setF({ ...form, provider: e.target.value, model: '', api_key: '' })}
            options={d.providers.map((p) => ({ value: p.id, label: p.label }))} />
        </Field>
        <Field label="Chave de API" hint={<>
          {sameProvider ? `Atual: ${d.key_masked}. Deixe em branco para manter. ` : ''}
          <a href={provider.key_url} target="_blank" rel="noreferrer">Onde pego a chave?</a>
          {form.provider === 'gemini' && ' O Gemini tem cota gratuita.'}
        </>}>
          <Input type="password" value={form.api_key} onChange={(e) => setF({ ...form, api_key: e.target.value })}
            autoComplete="off" required={!sameProvider} placeholder={sameProvider ? d.key_masked : 'Cole a chave aqui'} />
        </Field>
        <Field label="Modelo" hint={`Em branco: ${provider.default_model}`}>
          <Input value={form.model} onChange={(e) => setF({ ...form, model: e.target.value })} placeholder={provider.default_model} />
        </Field>
        {form.provider === 'openai' && (
          <Field label="Endereço compatível (opcional)" hint="Para OpenRouter, DeepSeek, Groq etc. Ex.: https://openrouter.ai/api/v1. Em branco: OpenAI.">
            <Input value={form.base_url} onChange={(e) => setF({ ...form, base_url: e.target.value })} placeholder="https://…" />
          </Field>
        )}
        <Check label="Usar IA no bot" checked={form.enabled} onChange={(v) => setF({ ...form, enabled: v })} />
        <p className="muted">Assinaturas como Claude Pro, ChatGPT Plus ou Gemini Advanced <b>não</b> incluem API: a chave é criada à parte no site do provedor.</p>
        {!d.can_edit && <p className="muted" role="note">Só o dono da família altera ou remove a chave de IA.</p>}
        <div className="row gap">
          {d.can_edit && <Button type="submit">Salvar IA</Button>}
          {d.configured && <Button type="button" variant="ghost" icon="stars" onClick={() => act(() => api.post<Done>('/settings/ai/test'))}>Testar</Button>}
          {d.configured && d.can_edit && (
            <ConfirmButton label="Remover chave de IA" text="Remover chave" confirmLabel="Confirmar remoção" onConfirm={() => act(() => api.del<Done>('/settings/ai'), done)} />
          )}
        </div>
      </form>
    </Panel>
  )
}
