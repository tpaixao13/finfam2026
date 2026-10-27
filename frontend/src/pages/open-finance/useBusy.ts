import { useState } from 'react'

/** Marca o botão como ocupado enquanto a ação roda (mesmo se ela falhar). */
export function useBusy(): [boolean, (fn: () => Promise<unknown>) => Promise<void>] {
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  return [busy, run]
}
