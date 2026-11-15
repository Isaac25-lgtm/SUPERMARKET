import { lazy, Suspense, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { primaryRole, useAuth } from '../auth/auth'
import { LoadingBlock } from '../components/ui'
import { AppShell } from '../layouts/AppShell'
import { LoginPage } from '../pages/Login'
import { ChangePasswordPage } from '../pages/ChangePassword'

const DashboardPage = lazy(() => import('../pages/Dashboard'))
const PosPage = lazy(() => import('../pages/Pos'))
const ApprovalsPage = lazy(() => import('../pages/Approvals'))
const ApprovalDetailPage = lazy(() => import('../pages/ApprovalDetail'))
const StockPage = lazy(() => import('../pages/Stock'))
const ProductPage = lazy(() => import('../pages/Product'))
const TransfersPage = lazy(() => import('../pages/Transfers'))
const TransferDetailPage = lazy(() => import('../pages/TransferDetail'))
const ProcurementPage = lazy(() => import('../pages/Procurement'))
const OrderDetailPage = lazy(() => import('../pages/OrderDetail'))
const FinancePage = lazy(() => import('../pages/Finance'))
const PayablesPage = lazy(() => import('../pages/Payables'))
const InvoiceDetailPage = lazy(() => import('../pages/InvoiceDetail'))
const SalesPage = lazy(() => import('../pages/Sales'))
const ShiftsPage = lazy(() => import('../pages/Shifts'))
const ReportsPage = lazy(() => import('../pages/Reports'))
const InsightsPage = lazy(() => import('../pages/Insights'))
const UsersPage = lazy(() => import('../pages/AdminUsers'))
const UserDetailPage = lazy(() => import('../pages/AdminUserDetail'))
const PoliciesPage = lazy(() => import('../pages/AdminPolicies'))
const AuditPage = lazy(() => import('../pages/Audit'))
const SystemPage = lazy(() => import('../pages/System'))

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
