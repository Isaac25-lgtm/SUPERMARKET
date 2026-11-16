import { lazy, Suspense, type ComponentType, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { primaryRole, useAuth } from '../auth/auth'
import { LoadingBlock } from '../components/ui'
import { AppShell } from '../layouts/AppShell'
import { LoginPage } from '../pages/Login'
import { ChangePasswordPage } from '../pages/ChangePassword'

// After a deploy, a tab that was already open asks for page chunks that no longer exist
// ("Failed to fetch dynamically imported module") and would render a blank page. Reload once
// to pick up the new build (index.html is served no-cache); a second failure surfaces normally.
const CHUNK_RELOAD_KEY = 'motion.chunk-reload'
function lazyPage(load: () => Promise<{ default: ComponentType }>) {
  return lazy(() => load().catch((err: unknown) => {
    let last = 0
    try { last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) ?? 0) } catch { /* storage unavailable */ }
    if (Date.now() - last > 30_000) {
      try { sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now())) } catch { /* ignore */ }
      window.location.reload()
      return new Promise<{ default: ComponentType }>(() => {})
    }
    throw err
  }))
}

const DashboardPage = lazyPage(() => import('../pages/Dashboard'))
const PosPage = lazyPage(() => import('../pages/Pos'))
const ApprovalsPage = lazyPage(() => import('../pages/Approvals'))
const ApprovalDetailPage = lazyPage(() => import('../pages/ApprovalDetail'))
const StockPage = lazyPage(() => import('../pages/Stock'))
const ProductPage = lazyPage(() => import('../pages/Product'))
const TransfersPage = lazyPage(() => import('../pages/Transfers'))
const TransferDetailPage = lazyPage(() => import('../pages/TransferDetail'))
const ProcurementPage = lazyPage(() => import('../pages/Procurement'))
const OrderDetailPage = lazyPage(() => import('../pages/OrderDetail'))
const FinancePage = lazyPage(() => import('../pages/Finance'))
const PayablesPage = lazyPage(() => import('../pages/Payables'))
const InvoiceDetailPage = lazyPage(() => import('../pages/InvoiceDetail'))
const SalesPage = lazyPage(() => import('../pages/Sales'))
const ShiftsPage = lazyPage(() => import('../pages/Shifts'))
const ReportsPage = lazyPage(() => import('../pages/Reports'))
const InsightsPage = lazyPage(() => import('../pages/Insights'))
const UsersPage = lazyPage(() => import('../pages/AdminUsers'))
const UserDetailPage = lazyPage(() => import('../pages/AdminUserDetail'))
const PoliciesPage = lazyPage(() => import('../pages/AdminPolicies'))
const AuditPage = lazyPage(() => import('../pages/Audit'))
const SystemPage = lazyPage(() => import('../pages/System'))

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="p-10"><LoadingBlock /></div>
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (me.must_change_password && location.pathname !== '/account/password') return <Navigate to="/account/password" replace />
  return <>{children}</>
}

function Guard({ perm, children }: { perm: string | string[]; children: ReactNode }) {
  const { can } = useAuth()
  const perms = Array.isArray(perm) ? perm : [perm]
  if (!perms.some(can)) {
    return (
      <div className="mx-auto mt-16 max-w-md text-center">
        <h2 className="text-lg font-semibold">Not available for your role</h2>
        <p className="mt-2 text-sm text-muted">This area is restricted. If you need access, ask the Head Manager to update your permissions.</p>
      </div>
    )
  }
  return <>{children}</>
}

function Home() {
  const { me } = useAuth()
  if (!me) return null
  const role = primaryRole(me.roles)
  if (role === 'cashier') return <Navigate to="/pos" replace />
  if (role === 'sysadmin') return <Navigate to="/system" replace />
  return <Navigate to="/dashboard" replace />
}

const page = (el: ReactNode) => <Suspense fallback={<LoadingBlock rows={6} />}>{el}</Suspense>

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={<Home />} />
          <Route path="/account/password" element={<ChangePasswordPage />} />
          <Route path="/dashboard" element={<Guard perm={['dashboard.branch', 'dashboard.consolidated']}>{page(<DashboardPage />)}</Guard>} />
          <Route path="/pos" element={<Guard perm="pos.sell">{page(<PosPage />)}</Guard>} />
          <Route path="/approvals" element={<Guard perm="approvals.inbox">{page(<ApprovalsPage />)}</Guard>} />
          <Route path="/approvals/:id" element={page(<ApprovalDetailPage />)} />
          <Route path="/stock" element={<Guard perm="stock.view">{page(<StockPage />)}</Guard>} />
          <Route path="/stock/products/:id" element={<Guard perm="catalog.view">{page(<ProductPage />)}</Guard>} />
          <Route path="/transfers" element={<Guard perm={['stock.view', 'transfer.request']}>{page(<TransfersPage />)}</Guard>} />
          <Route path="/transfers/:id" element={<Guard perm="stock.view">{page(<TransferDetailPage />)}</Guard>} />
          <Route path="/procurement" element={<Guard perm={['procurement.request', 'stock.receive']}>{page(<ProcurementPage />)}</Guard>} />
          <Route path="/procurement/orders/:id" element={<Guard perm={['procurement.request', 'stock.receive', 'finance.view']}>{page(<OrderDetailPage />)}</Guard>} />
          <Route path="/finance" element={<Guard perm="finance.view">{page(<FinancePage />)}</Guard>} />
          <Route path="/payables" element={<Guard perm={['finance.view', 'expense.submit']}>{page(<PayablesPage />)}</Guard>} />
          <Route path="/payables/invoices/:id" element={<Guard perm="finance.view">{page(<InvoiceDetailPage />)}</Guard>} />
          <Route path="/sales" element={<Guard perm={['pos.history.branch', 'pos.history.own']}>{page(<SalesPage />)}</Guard>} />
          <Route path="/shifts" element={<Guard perm="pos.history.branch">{page(<ShiftsPage />)}</Guard>} />
          <Route path="/reports" element={<Guard perm="reports.export">{page(<ReportsPage />)}</Guard>} />
          <Route path="/insights" element={<Guard perm="insights.view">{page(<InsightsPage />)}</Guard>} />
          <Route path="/admin/users" element={<Guard perm="admin.users">{page(<UsersPage />)}</Guard>} />
          <Route path="/admin/users/:id" element={<Guard perm="admin.users">{page(<UserDetailPage />)}</Guard>} />
          <Route path="/admin/policies" element={<Guard perm={['admin.policies', 'admin.users']}>{page(<PoliciesPage />)}</Guard>} />
          <Route path="/audit" element={<Guard perm="audit.view">{page(<AuditPage />)}</Guard>} />
          <Route path="/system" element={<Guard perm={['system.health', 'sync.devices']}>{page(<SystemPage />)}</Guard>} />
          <Route path="*" element={<div className="mt-16 text-center"><h2 className="text-lg font-semibold">Page not found</h2></div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
