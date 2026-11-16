import * as Menu from '@radix-ui/react-dropdown-menu'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  ArrowLeftRight, BarChart3, Boxes, Building2, ChevronsLeft, ClipboardCheck, FileSpreadsheet, KeyRound, LayoutDashboard,
  LogOut, Menu as MenuIcon, Moon, ReceiptText, ScanBarcode, ScrollText, ServerCog, ShieldCheck, Sparkles,
  Sun, Timer, Truck, Users, Wallet,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { ROLE_LABELS, primaryRole, useAuth } from '../auth/auth'
import { useIdleLogout } from '../auth/useIdleLogout'
import { AskMotionFloater } from '../components/MotionChat'
import { type AccessTab, MyAccessDialog } from '../components/MyAccess'
import { TestDataBadge } from '../components/ui'

interface NavItem { to: string; label: string; icon: ReactNode; show: (can: (p: string) => boolean) => boolean; badge?: number }

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => {
    try { return localStorage.getItem('motion.theme') ?? 'light' } catch { return 'light' }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('motion.theme', theme) } catch { /* ignore */ }
  }, [theme])
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))]
}

export function AppShell() {
  const { me, meta, can, branchId, setBranchId, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [theme, toggleTheme] = useTheme()
  const inbox = useQuery({
    queryKey: ['inbox-count'],
    queryFn: () => api.get<{ actionable: unknown[] }>('/api/approvals/inbox'),
    enabled: can('approvals.inbox'),
    refetchInterval: 60_000,
  })
  useEffect(() => setMobileOpen(false), [location.pathname])
  const [accessOpen, setAccessOpen] = useState<AccessTab | null>(null)
  const onIdle = useCallback(() => { void logout('idle') }, [logout])
  useIdleLogout(meta?.session_idle_minutes ?? 5, onIdle)
  if (!me) return null
  const isCashierOnly = me.roles.length === 1 && me.roles[0] === 'cashier'
  const nav: { section: string; items: NavItem[] }[] = [
    { section: 'Overview', items: [
      { to: '/dashboard', label: 'Dashboard', icon: <LayoutDashboard />, show: (c) => c('dashboard.branch') || c('dashboard.consolidated') },
      { to: '/pos', label: 'Point of sale', icon: <ScanBarcode />, show: (c) => c('pos.sell') },
      { to: '/approvals', label: 'Approvals', icon: <ClipboardCheck />, show: (c) => c('approvals.inbox'), badge: inbox.data?.actionable.length },
      { to: '/insights', label: 'Alerts & insights', icon: <Sparkles />, show: (c) => c('insights.view') },
    ] },
    { section: 'Operations', items: [
      { to: '/sales', label: 'Sales & receipts', icon: <ReceiptText />, show: (c) => c('pos.history.branch') || c('pos.history.own') },
      { to: '/shifts', label: 'Shifts & cash', icon: <Timer />, show: (c) => c('pos.history.branch') },
      { to: '/stock', label: 'Stock & products', icon: <Boxes />, show: (c) => c('stock.view') },
      { to: '/transfers', label: 'Transfers', icon: <ArrowLeftRight />, show: (c) => c('transfer.request') || c('transfer.dispatch') || c('stock.view') },
      { to: '/procurement', label: 'Procurement', icon: <Truck />, show: (c) => c('procurement.request') || c('stock.receive') },
    ] },
    { section: 'Finance', items: [
      { to: '/finance', label: 'Profit & cash', icon: <BarChart3 />, show: (c) => c('finance.view') },
      { to: '/payables', label: 'Payables & expenses', icon: <Wallet />, show: (c) => c('finance.view') || c('expense.submit') },
      { to: '/reports', label: 'Reports', icon: <FileSpreadsheet />, show: (c) => c('reports.export') },
    ] },
    { section: 'Administration', items: [
      { to: '/admin/users', label: 'Users & access', icon: <Users />, show: (c) => c('admin.users') },
      { to: '/admin/policies', label: 'Approval policies', icon: <ShieldCheck />, show: (c) => c('admin.policies') },
      { to: '/audit', label: 'Audit trail', icon: <ScrollText />, show: (c) => c('audit.view') },
      { to: '/system', label: 'System & devices', icon: <ServerCog />, show: (c) => c('system.health') || c('sync.devices') },
    ] },
  ]
  const sidebar = (
    <nav aria-label="Main" className="flex h-full flex-col">
      <Link to="/" title="Go to my dashboard" aria-label={`${meta?.brand ?? 'Motion Retail'} — go to my dashboard`}
        className={clsx('flex h-16 items-center gap-2.5 border-b border-line px-4 transition hover:bg-surface-2', collapsed && 'justify-center px-2')}>
        <img src="/favicon.svg" alt="" className="size-8 shrink-0" />
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <div className="truncate text-[14.5px] font-semibold tracking-tight">{meta?.brand ?? 'Motion Retail'}</div>
            <div className="truncate text-[11.5px] text-muted">by Motion Imprints</div>
          </div>
        )}
      </Link>
      <div className="flex-1 overflow-y-auto px-2.5 py-3">
        {nav.map((sec) => {
          const items = sec.items.filter((i) => i.show(can))
          if (!items.length) return null
          return (
            <div key={sec.section} className="mb-4">
              {!collapsed && <div className="px-2.5 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted/80">{sec.section}</div>}
              {items.map((i) => (
                <NavLink key={i.to} to={i.to} title={collapsed ? i.label : undefined}
                  className={({ isActive }) => clsx('group mb-0.5 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition',
                    collapsed && 'justify-center', isActive ? 'bg-accent-soft text-accent-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink')}>
                  <span className="[&>svg]:size-[18px]">{i.icon}</span>
                  {!collapsed && <span className="flex-1 truncate">{i.label}</span>}
                  {!collapsed && !!i.badge && <span className="rounded-full bg-accent px-1.5 text-[11px] font-semibold text-white dark:text-canvas">{i.badge}</span>}
                </NavLink>
              ))}
            </div>
          )
        })}
      </div>
      <button onClick={() => setCollapsed((v) => !v)} className="hidden items-center gap-2 border-t border-line px-4 py-3 text-[13px] text-muted hover:text-ink lg:flex" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
        <ChevronsLeft className={clsx('size-4 transition', collapsed && 'rotate-180')} />{!collapsed && 'Collapse'}
      </button>
    </nav>
  )
  return (
    <div className="flex h-full">
      <aside className={clsx('hidden shrink-0 border-r border-line bg-surface transition-all lg:block', collapsed ? 'w-[68px]' : 'w-[248px]')}>{sidebar}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-ink/30" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-[264px] border-r border-line bg-surface">{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur sm:px-6">
          <button className="rounded-lg p-2 text-ink-2 hover:bg-surface-2 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu"><MenuIcon className="size-5" /></button>
          {me.branches.length > 1 && !isCashierOnly ? (
            <label className="flex items-center gap-2 text-[13px]">
              <Building2 className="size-4 text-muted" aria-hidden />
              <span className="sr-only">Branch scope</span>
              <select value={branchId ?? ''} onChange={(e) => setBranchId(e.target.value ? Number(e.target.value) : null)}
                className="h-9 rounded-lg border border-line-strong bg-surface px-2.5 text-[13px] font-medium">
                <option value="">All my branches ({me.branches.length})</option>
                {me.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </label>
          ) : (
            <span className="flex items-center gap-2 text-[13px] font-medium text-ink-2"><Building2 className="size-4 text-muted" />{me.branches[0]?.name ?? 'No branch'}</span>
          )}
          <div className="flex-1" />
          {meta?.test_data && <TestDataBadge />}
          <button onClick={toggleTheme} className="rounded-lg p-2 text-ink-2 hover:bg-surface-2" aria-label="Toggle dark mode">{theme === 'dark' ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}</button>
          <Menu.Root>
            <Menu.Trigger className="flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-2 hover:bg-surface-2" aria-label="Account menu">
              <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent-ink">
                {me.full_name.split(' ').slice(0, 2).map((s) => s[0]).join('')}
              </span>
              <span className="hidden text-left leading-tight sm:block">
                <span className="block max-w-[180px] truncate text-[13px] font-medium">{me.full_name.replace(/\s*\(TEST.*\)/, '')}</span>
                <span className="block text-[11.5px] text-muted">{ROLE_LABELS[primaryRole(me.roles)]}</span>
              </span>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content align="end" sideOffset={6} className="z-50 min-w-[220px] rounded-xl border border-line-strong bg-surface p-1.5 shadow-[var(--shadow-pop)]">
                <div className="px-2.5 py-2 text-[12px] text-muted">Signed in as <b className="text-ink">{me.username}</b><br />{me.roles.map((r) => ROLE_LABELS[r]).join(', ')}</div>
                <Menu.Separator className="my-1 h-px bg-line" />
                <Menu.Item onSelect={() => setAccessOpen('mine')} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] outline-none data-[highlighted]:bg-surface-2"><ShieldCheck className="size-4" />My access</Menu.Item>
                <Menu.Item onSelect={() => setAccessOpen('roles')} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] outline-none data-[highlighted]:bg-surface-2"><Users className="size-4" />Roles &amp; permissions</Menu.Item>
                {!meta?.open_demo && <Menu.Item onSelect={() => navigate('/account/password')} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] outline-none data-[highlighted]:bg-surface-2"><KeyRound className="size-4" />Change password</Menu.Item>}
                {meta?.open_demo && <Menu.Item onSelect={() => { void logout() }} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] outline-none data-[highlighted]:bg-surface-2"><Users className="size-4" />Switch demo account</Menu.Item>}
                <Menu.Item onSelect={() => { void logout() }} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-bad outline-none data-[highlighted]:bg-bad-soft"><LogOut className="size-4" />Sign out</Menu.Item>
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1440px] px-4 pb-24 pt-6 sm:px-6 lg:px-8"><Outlet /></div>
        </main>
        <AskMotionFloater />
        <MyAccessDialog open={accessOpen !== null} tab={accessOpen ?? 'mine'} onTab={setAccessOpen} onOpenChange={(v) => { if (!v) setAccessOpen(null) }} />
      </div>
    </div>
  )
}

