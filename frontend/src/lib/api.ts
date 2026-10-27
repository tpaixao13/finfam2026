// Cliente HTTP da API do FinFam. Guarda o token no localStorage.

const BASE = '/api/v1'
const TOKEN_KEY = 'finfam.token'
const ADMIN_KEY = 'finfam.admin_token'

export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

function storageGet(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function storageSet(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch { /* modo privado */ }
}

export const tokens = {
  user: () => storageGet(TOKEN_KEY),
  setUser: (t: string | null) => storageSet(TOKEN_KEY, t),
  admin: () => storageGet(ADMIN_KEY),
  setAdmin: (t: string | null) => storageSet(ADMIN_KEY, t),
}

type Listener = (status: number, code?: string) => void
const listeners = new Set<Listener>()
/** Notificado quando a API responde 401 (sessão expirada) ou 402 (plano expirado). */
export function onAuthProblem(fn: Listener) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

type Query = Record<string, string | number | boolean | null | undefined>

export function qs(params?: Query): string {
  if (!params) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

interface Options {
  method?: string
  body?: unknown
  query?: Query
  admin?: boolean
  raw?: boolean
}

async function request<T>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = opts.admin ? tokens.admin() : tokens.user()
  if (token) headers.Authorization = `Bearer ${token}`
  let body: BodyInit | undefined
  if (opts.body instanceof FormData) body = opts.body
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  const res = await fetch(BASE + path + qs(opts.query), { method: opts.method ?? 'GET', headers, body })
  if (!res.ok) {
    let message = `Erro ${res.status}`
    let code: string | undefined
    try {
      const data = await res.json()
      const d = data?.detail
      if (typeof d === 'string') message = d
      else if (d && typeof d === 'object') { message = d.message ?? message; code = d.code }
    } catch { /* corpo não-JSON */ }
    if (!opts.admin && (res.status === 401 || res.status === 402)) listeners.forEach((fn) => fn(res.status, code))
    throw new ApiError(res.status, message, code)
  }
  if (opts.raw) return res as unknown as T
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>(path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  admin: {
    get: <T>(path: string) => request<T>(path, { admin: true }),
    post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body, admin: true }),
    put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body, admin: true }),
    del: <T>(path: string) => request<T>(path, { method: 'DELETE', admin: true }),
  },
  /** Baixa um arquivo (export xlsx/pdf) usando o token (admin: token do admin). */
  async download(path: string, query: Query, fallbackName: string, admin = false) {
    const res = await request<Response>(path, { query, raw: true, admin })
    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const name = /filename=([^;]+)/.exec(disposition)?.[1] ?? fallbackName
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}
