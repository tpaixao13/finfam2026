// Estado global: usuário logado, listas de referência, toasts e modal de despesa.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, onAuthProblem, tokens } from './api'
import { navigate } from './router'
import type { Expense, Me, Meta } from './types'

export type ToastKind = 'success' | 'danger' | 'warning' | 'info'
interface Toast { id: number; kind: ToastKind; text: string }

interface ExpenseModalState { open: boolean; expense?: Expense | null; preset?: Partial<Expense> }

interface SessionValue {
  me: Me | null
  meta: Meta | null
  loading: boolean
  planExpired: boolean
  refreshMe: () => Promise<void>
  login: (token: string) => Promise<void>
  logout: () => void
  toast: (text: string, kind?: ToastKind) => void
  toasts: Toast[]
  dismissToast: (id: number) => void
  expenseModal: ExpenseModalState
  openExpense: (expense?: Expense | null, preset?: Partial<Expense>) => void
  closeExpense: () => void
  /** Incrementa quando dados mudam fora da página (ex.: despesa salva no modal). */
  dataVersion: number
  bumpData: () => void
}

const Ctx = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [loading, setLoading] = useState(Boolean(tokens.user()))
  const [planExpired, setPlanExpired] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [expenseModal, setExpenseModal] = useState<ExpenseModalState>({ open: false })
  const [dataVersion, setDataVersion] = useState(0)

  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const toast = useCallback((text: string, kind: ToastKind = 'success') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t.slice(-3), { id, kind, text }])
    setTimeout(() => dismissToast(id), 4500)
  }, [dismissToast])

  const refreshMe = useCallback(async () => {
    if (!tokens.user()) { setMe(null); setLoading(false); return }
    try {
      const data = await api.get<Me>('/auth/me')
      setMe(data)
      setPlanExpired(Boolean(data.tenant && !data.tenant.active))
    } catch {
      setMe(null)
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(() => {
    tokens.setUser(null)
    setMe(null)
    setPlanExpired(false)
    navigate('/login', { replace: true })
  }, [])

  const login = useCallback(async (token: string) => {
    tokens.setUser(token)
    setLoading(true)
    await refreshMe()
  }, [refreshMe])

  useEffect(() => { refreshMe() }, [refreshMe])
  useEffect(() => { api.get<Meta>('/meta').then(setMeta).catch(() => undefined) }, [])
  useEffect(() => onAuthProblem((status) => {
    if (status === 401) { tokens.setUser(null); setMe(null); navigate('/login', { replace: true }) }
    if (status === 402) setPlanExpired(true)
  }), [])

  const value = useMemo<SessionValue>(() => ({
    me, meta, loading, planExpired, refreshMe, login, logout, toast, toasts, dismissToast,
    expenseModal,
    openExpense: (expense, preset) => setExpenseModal({ open: true, expense: expense ?? null, preset }),
    closeExpense: () => setExpenseModal({ open: false }),
    dataVersion,
    bumpData: () => { setDataVersion((v) => v + 1); refreshMe() },
  }), [me, meta, loading, planExpired, refreshMe, login, logout, toast, toasts, dismissToast, expenseModal, dataVersion])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useSession(): SessionValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useSession fora do SessionProvider')
  return v
}

/** Executa uma ação da API mostrando toast de sucesso/erro. */
export function useAction() {
  const { toast } = useSession()
  return useCallback(async <T extends { message?: string }>(fn: () => Promise<T>, onOk?: (r: T) => void) => {
    try {
      const r = await fn()
      if (r?.message) toast(r.message, 'success')
      onOk?.(r)
      return r
    } catch (e) {
      toast((e as Error).message, 'danger')
      return null
    }
  }, [toast])
}
