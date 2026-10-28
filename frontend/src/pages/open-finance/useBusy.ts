import { useState } from 'react'
import { useAction } from '../../lib/session'

/** Marca o botão como ocupado enquanto a ação roda (mesmo se ela falhar). */
export function useBusy(): [boolean, (fn: () => Promise<unknown>) => Promise<void>] {
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  return [busy, run]
}

type Reply = { message?: string }

/** `useAction` (toast com a mensagem da API) + `useBusy`: para botões que chamam a API e esperam. */
export function useBusyAction(): [boolean, <T extends Reply>(fn: () => Promise<T>, onOk?: (r: T) => void) => Promise<void>] {
  const act = useAction()
  const [busy, run] = useBusy()
  return [busy, (fn, onOk) => run(() => act(fn, onOk))]
}
