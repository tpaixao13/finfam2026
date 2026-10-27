// Versão da API (GET /health), discreta: só mostra quando chega; falhou → nada.
import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export function AppVersion({ className = '' }: { className?: string }) {
  const [version, setVersion] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api.get<{ version?: string }>('/health').then((r) => { if (alive && r.version) setVersion(r.version) }).catch(() => {})
    return () => { alive = false }
  }, [])
  return version ? <p className={`app-version ${className}`}>FinFam {version}</p> : null
}
