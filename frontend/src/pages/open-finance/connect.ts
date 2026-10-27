// Widget Pluggy Connect: o script só é carregado quando a pessoa toca em "Conectar banco".
import { api } from '../../lib/api'

const CONNECT_JS = 'https://cdn.pluggy.ai/pluggy-connect/v2.7.0/pluggy-connect.js'
type ConnectOpts = {
  connectToken: string; includeSandbox?: boolean; updateItem?: string
  onSuccess: (d: { item: { id: string } }) => void; onError?: (e: { message?: string }) => void; onClose?: () => void
}
declare global { interface Window { PluggyConnect?: new (o: ConnectOpts) => { init: () => void } } }
let loading: Promise<void> | null = null

function loadConnect(): Promise<void> {
  if (window.PluggyConnect) return Promise.resolve()
  loading ??= new Promise((ok, fail) => {
    const s = document.createElement('script')
    s.src = CONNECT_JS
    s.async = true
    s.onload = () => ok()
    s.onerror = () => { loading = null; fail(new Error('Não consegui abrir o Pluggy Connect. Confira a internet e tente de novo.')) }
    document.head.appendChild(s)
  })
  return loading
}

/** Abre o Pluggy Connect; resolve com o id do item conectado (ou null se a pessoa fechou). */
export async function openConnect(connectionId?: number): Promise<string | null> {
  const [tok] = await Promise.all([
    api.post<{ connect_token: string; item_id: string | null; include_sandbox: boolean }>(
      '/pluggy/connect-token', { connection_id: connectionId ?? null }),
    loadConnect(),
  ])
  return new Promise((resolve, reject) => {
    new window.PluggyConnect!({
      connectToken: tok.connect_token,
      includeSandbox: tok.include_sandbox,
      ...(tok.item_id ? { updateItem: tok.item_id } : {}),
      onSuccess: ({ item }) => resolve(item.id),
      onError: (e) => reject(new Error(e?.message || 'O banco não conectou. Tente de novo.')),
      onClose: () => resolve(null),
    }).init()
  })
}
