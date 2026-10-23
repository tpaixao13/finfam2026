import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from './router'

/** Carrega dados da API; recarrega quando `deps` mudam. */
export function useApi<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)

  const load = useCallback(() => {
    const id = ++seq.current
    setLoading(true)
    return loader()
      .then((d) => { if (id === seq.current) { setData(d); setError(null) } })
      .catch((e: Error) => { if (id === seq.current) setError(e.message) })
      .finally(() => { if (id === seq.current) setLoading(false) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => { load() }, [load])
  return { data, error, loading, reload: load, setData }
}

/**
 * Estado de formulário em modal: volta ao `initial()` a cada abertura (limpando o erro)
 * e `run` controla envio — `busy` enquanto roda, mensagem da exceção em `error`.
 */
export function useFormState<S>(open: boolean, initial: () => S) {
  const [f, setF] = useState<S>(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastOpen, setLastOpen] = useState(false)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) { setF(initial()); setError(null) }
  }
  const set = useCallback(<K extends keyof S>(k: K, v: S[K]) => setF((s) => ({ ...s, [k]: v })), [])
  const run = async (task: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try { await task() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return { f, setF, set, busy, error, setError, run }
}

/** Mês/ano selecionados, guardados na URL (?month=&year=). */
export function usePeriod() {
  const [params, set] = useSearchParams()
  const now = new Date()
  const month = Number(params.get('month')) || now.getMonth() + 1
  const year = Number(params.get('year')) || now.getFullYear()
  const setPeriod = (m: number, y: number) => set({ month: m, year: y })
  return { month, year, setPeriod }
}
