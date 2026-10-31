import { TermsBanner } from '../pages/Legal'
import { TwoFactorBanner } from './TwoFactor'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useLocation } from '../lib/router'
import { useSession } from '../lib/session'
import { Icon } from './Icon'
import { Avatar, Button, IconButton } from './ui'

type NavItem = { to: string; icon: string; label: string; openFinance?: boolean }
const NAV: { section: string; items: NavItem[] }[] = [
  { section: 'Dinheiro', items: [
    { to: '/', icon: 'speedometer2', label: 'Painel' },
    { to: '/expenses', icon: 'list-ul', label: 'Despesas' },
    { to: '/bills', icon: 'calendar-check', label: 'Contas a pagar' },
    { to: '/incomes', icon: 'cash-coin', label: 'Rendas' },
    { to: '/cards', icon: 'credit-card-2-front', label: 'Cartões' },
    { to: '/investments', icon: 'graph-up-arrow', label: 'Investimentos' },
    { to: '/goals', icon: 'trophy', label: 'Metas' },
  ] },
  { section: 'Automação', items: [
    { to: '/alerts', icon: 'bell', label: 'Alertas' },
    { to: '/import', icon: 'cloud-upload', label: 'Importar extrato' },
    { to: '/bank', icon: 'bank', label: 'Contas conectadas', openFinance: true },
    { to: '/rules', icon: 'tags', label: 'Regras de categoria' },
  ] },
  { section: 'Conta', items: [
    { to: '/family', icon: 'people-fill', label: 'Família' },
    { to: '/settings', icon: 'gear', label: 'Perfil e acesso' },
    { to: '/telegram', icon: 'telegram', label: 'Telegram e IA' },
  ] },
]

// Atalho "Nova despesa" no topo: só nas telas do dia a dia que não têm botão próprio.
// Despesas, Rendas, Investimentos e Metas já trazem a ação delas; telas de conta/configuração não lançam gastos.
const QUICK_EXPENSE = ['/', '/bills', '/cards', '/alerts']
const showQuickExpense = (pathname: string) => QUICK_EXPENSE.some((to) => isActive(to, pathname))

function isActive(to: string, pathname: string) {
  return to === '/' ? pathname === '/' : pathname === to || pathname.startsWith(to + '/')
}

export function Layout({ children }: { children: ReactNode }) {
  const { me, logout, openExpense } = useSession()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)
  useEffect(() => setOpen(false), [pathname])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  const alerts = me?.alert_count ?? 0
  const plan = me?.tenant?.plan

  return (
    <div className={`shell ${open ? 'nav-open' : ''}`}>
      <aside id="sidebar" className="sidebar" aria-label="Navegação principal">
        <div className="brand">
          <img src="/logo.svg" alt="" width={30} height={30} />
          <span>FinFam</span>
        </div>
        <nav>
          {NAV.map((group) => (
            <div key={group.section} className="nav-group">
              <span className="nav-section">{group.section}</span>
              {group.items.filter((item) => !item.openFinance || me?.tenant?.open_finance).map((item) => (
                <Link key={item.to} to={item.to} className={`nav-link ${isActive(item.to, pathname) ? 'active' : ''}`}
                  aria-current={isActive(item.to, pathname) ? 'page' : undefined}>
                  <Icon name={item.to === '/alerts' && alerts ? 'bell-fill' : item.icon} />
                  <span>{item.label}</span>
                  {item.to === '/alerts' && alerts > 0 && <span className="nav-count">{alerts}</span>}
                </Link>
              ))}
            </div>
          ))}
          {plan && plan !== 'vitalicio' && (
            <Link to="/upgrade" className={`nav-link nav-upgrade ${pathname === '/upgrade' ? 'active' : ''}`}>
              <Icon name="rocket-takeoff" />
              <span>Plano {me?.tenant?.plan_label}</span>
              {me?.tenant?.days_left !== null && me?.tenant?.days_left !== undefined && (
                <span className="nav-count nav-count-soft">{me.tenant.days_left}d</span>
              )}
            </Link>
          )}
        </nav>
        <div className="sidebar-foot">
          {me && (
            <div className="who">
              <Avatar name={me.user.name} url={me.user.avatar_url} size={34} />
              <div>
                <strong>{me.user.name}</strong>
                <small>{me.tenant?.name}</small>
              </div>
            </div>
          )}
          <button className="nav-link nav-logout" onClick={logout}><Icon name="box-arrow-right" /><span>Sair</span></button>
          <p className="sidebar-legal"><Link to="/termos">Termos</Link> · <Link to="/privacidade">Privacidade</Link></p>
        </div>
      </aside>
      <div className="scrim" onClick={() => setOpen(false)} />
      <div className="main">
        <header className="topbar">
          <IconButton className="menu-btn" icon="list" label="Abrir menu" aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen(true)} />
          <div className="topbar-spacer" />
          <Link to="/alerts" className="topbar-bell" aria-label={`${alerts} alertas`}>
            <Icon name={alerts ? 'bell-fill' : 'bell'} />
            {alerts > 0 && <span>{alerts}</span>}
          </Link>
          {showQuickExpense(pathname) && <Button icon="plus" onClick={() => openExpense()}>Nova despesa</Button>}
        </header>
        <main className="content"><TermsBanner /><TwoFactorBanner />{children}</main>
      </div>
    </div>
  )
}

export function Toasts() {
  const { toasts, dismissToast } = useSession()
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          <Icon name={t.kind === 'success' ? 'check-circle-fill' : t.kind === 'danger' ? 'x-circle-fill' : 'info-circle'} />
          <span>{t.text}</span>
          <button aria-label="Fechar" onClick={() => dismissToast(t.id)}><Icon name="x" /></button>
        </div>
      ))}
    </div>
  )
}
