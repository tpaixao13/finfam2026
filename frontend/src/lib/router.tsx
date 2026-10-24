// Roteador mínimo baseado na History API (sem dependências).
import { createContext, useContext, useEffect, useMemo, useState, type AnchorHTMLAttributes, type MouseEvent } from 'react'

interface Location { pathname: string; search: string }

const RouterCtx = createContext<Location>({ pathname: '/', search: '' })

function current(): Location {
  return { pathname: window.location.pathname, search: window.location.search }
}

export function navigate(to: string, opts: { replace?: boolean } = {}) {
  if (opts.replace) window.history.replaceState(null, '', to)
  else window.history.pushState(null, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
  if (!opts.replace) window.scrollTo(0, 0)
}

export function RouterProvider({ children }: { children: React.ReactNode }) {
  const [loc, setLoc] = useState<Location>(current)
  useEffect(() => {
    const onPop = () => setLoc(current())
    window.addEventListener('popstate', onPop)
    // uma página filha pode ter chamado navigate() antes deste efeito (os efeitos dos filhos
    // rodam primeiro), ex.: /admin sem login → /admin/login. Sincroniza com a URL de agora.
    onPop()
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return <RouterCtx.Provider value={loc}>{children}</RouterCtx.Provider>
}

export function useLocation() {
  return useContext(RouterCtx)
}

export function useSearchParams(): [URLSearchParams, (next: Record<string, string | number | null | undefined>, replace?: boolean) => void] {
  const { pathname, search } = useLocation()
  const params = useMemo(() => new URLSearchParams(search), [search])
  const set = (next: Record<string, string | number | null | undefined>, replace = true) => {
    const sp = new URLSearchParams(search)
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === undefined || v === '') sp.delete(k)
      else sp.set(k, String(v))
    }
    const s = sp.toString()
    navigate(pathname + (s ? `?${s}` : ''), { replace })
  }
  return [params, set]
}

/** Casa um padrão como "/cards/:id/invoice" com o caminho atual. */
export function matchPath(pattern: string, pathname: string): Record<string, string> | null {
  const p = pattern.split('/').filter(Boolean)
  const a = pathname.split('/').filter(Boolean)
  if (p.length !== a.length) return null
  const params: Record<string, string> = {}
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(':')) params[p[i].slice(1)] = decodeURIComponent(a[i])
    else if (p[i] !== a[i]) return null
  }
  return params
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }

export function Link({ to, onClick, ...rest }: LinkProps) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e)
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={handle} {...rest} />
}
