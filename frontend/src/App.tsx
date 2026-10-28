import { useEffect } from 'react'
import { ExpenseModal } from './components/ExpenseModal'
import { Layout, Toasts } from './components/Layout'
import { Spinner } from './components/ui'
import { matchPath, navigate, useLocation } from './lib/router'
import { useSession } from './lib/session'
import { OpenFinancePage } from './pages/OpenFinance'
import { OpenFinanceSettingsPage } from './pages/OpenFinanceSettings'
import { ReviewImportsPage } from './pages/ReviewImports'
import { FamilyPage, SettingsPage, UpgradePage } from './pages/Account'
import { TelegramPage } from './pages/Telegram'
import { AdminLoginPage, AdminPage } from './pages/admin/Admin'
import { ConfirmEmailPage, ForgotPasswordPage, ResetPasswordPage } from './pages/AccountEmail'
import { LoginPage, RegisterPage } from './pages/Auth'
import { AlertsPage, ImportPage, RulesPage } from './pages/Automation'
import { CardsPage, InvoicePage } from './pages/Cards'
import { DashboardPage } from './pages/Dashboard'
import { BillsPage, ExpensesPage } from './pages/Expenses'
import { GoalsPage } from './pages/Goals'
import { IncomesPage } from './pages/Incomes'
import { InvestmentsPage } from './pages/Investments'

const ROUTES: [string, (p: Record<string, string>) => React.ReactNode][] = [
  ['/', () => <DashboardPage />],
  ['/expenses', () => <ExpensesPage />],
  ['/bills', () => <BillsPage />],
  ['/incomes', () => <IncomesPage />],
  ['/cards', () => <CardsPage />],
  ['/cards/:id/invoice', (p) => <InvoicePage id={p.id} />],
  ['/investments', () => <InvestmentsPage />],
  ['/goals', () => <GoalsPage />],
  ['/alerts', () => <AlertsPage />],
  ['/import', () => <ImportPage />],
  ['/rules', () => <RulesPage />],
  ['/family', () => <FamilyPage />],
  ['/settings', () => <SettingsPage />],
  ['/telegram', () => <TelegramPage />],
  ['/bank', () => <OpenFinancePage />],
  ['/bank/review', () => <ReviewImportsPage />],
  ['/bank/settings', () => <OpenFinanceSettingsPage />],
  ['/upgrade', () => <UpgradePage />],
]

const TITLES: Record<string, string> = {
  '/': 'Painel', '/expenses': 'Despesas', '/bills': 'Contas a pagar', '/incomes': 'Rendas', '/cards': 'Cartões',
  '/investments': 'Investimentos', '/goals': 'Metas', '/alerts': 'Alertas', '/import': 'Importar extrato',
  '/rules': 'Regras de categoria', '/family': 'Família', '/settings': 'Perfil e acesso', '/telegram': 'Telegram e IA', '/bank': 'Contas conectadas', '/bank/review': 'Revisar importados',
  '/bank/settings': 'Configurações do Open Finance',
  '/upgrade': 'Planos', '/login': 'Entrar', '/register': 'Criar conta',
  '/esqueci-senha': 'Esqueci minha senha', '/redefinir-senha': 'Nova senha', '/confirmar-email': 'Confirmar e-mail',
}

function NotFound() {
  return <div className="page"><h1>Página não encontrada</h1><p><a href="/">Voltar ao painel</a></p></div>
}

export function App() {
  const { pathname } = useLocation()
  const { me, loading, planExpired } = useSession()

  useEffect(() => {
    document.title = `${TITLES[pathname] ?? (pathname.startsWith('/cards/') ? 'Fatura' : 'FinFam')} · FinFam`
  }, [pathname])

  if (pathname === '/admin/login') return <><AdminLoginPage /><Toasts /></>
  if (pathname === '/admin') return <><AdminPage /><Toasts /></>
  // links do e-mail: valem logado ou não
  if (pathname === '/confirmar-email') return <><ConfirmEmailPage /><Toasts /></>
  if (pathname === '/redefinir-senha') return <><ResetPasswordPage /><Toasts /></>

  const isPublic = pathname === '/login' || pathname === '/register' || pathname === '/esqueci-senha'
  if (loading) return <Spinner label="Abrindo o FinFam…" />
  if (!me) {
    if (!isPublic) { navigate('/login', { replace: true }); return null }
    const publicPage = pathname === '/register' ? <RegisterPage />
      : pathname === '/esqueci-senha' ? <ForgotPasswordPage /> : <LoginPage />
    return <>{publicPage}<Toasts /></>
  }
  if (isPublic) { navigate('/', { replace: true }); return null }

  let page: React.ReactNode = <NotFound />
  if (planExpired) page = <UpgradePage expired />
  else {
    for (const [pattern, render] of ROUTES) {
      const params = matchPath(pattern, pathname)
      if (params) { page = render(params); break }
    }
  }

  return (
    <>
      <Layout>{page}</Layout>
      <ExpenseModal />
      <Toasts />
    </>
  )
}
