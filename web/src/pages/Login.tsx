import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { Lock, ShieldCheck, Store, WifiOff } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { api } from '../api/client'
import { ROLE_LABELS, primaryRole, useAuth } from '../auth/auth'
import { NatureLeaves } from '../components/NatureLeaves'
import { RoleIcon } from '../components/RoleIcon'
import { Button, ErrorBox, Field, Input, TestDataBadge } from '../components/ui'

const schema = z.object({
  username: z.string().trim().min(1, 'Enter your username'),
  password: z.string().min(1, 'Enter your password'),
})
type Form = z.infer<typeof schema>

export function LoginPage() {
  const { me, meta } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [error, setError] = useState<unknown>(null)
  const { register, handleSubmit, formState } = useForm<Form>({ resolver: zodResolver(schema) })
  if (me) return <Navigate to="/" replace />
  if (meta?.open_demo) return <DemoPicker brand={meta.brand} />
  const onSubmit = handleSubmit(async (values) => {
    setError(null)
    try {
      await api.post('/api/auth/login', values)
      await qc.invalidateQueries({ queryKey: ['me'] })
      navigate('/', { replace: true })
    } catch (e) {
      setError(e)
    }
  })
  return (
    <div className="grid min-h-full lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden overflow-hidden bg-accent-ink p-12 text-white lg:flex lg:flex-col">
        <div className="absolute -right-24 -top-24 size-[420px] rounded-full bg-white/5" />
        <div className="absolute -bottom-32 -left-16 size-[360px] rounded-full bg-white/5" />
        <div className="relative flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-10" />
          <div>
            <div className="text-lg font-semibold">{meta?.brand ?? 'Motion Retail'}</div>
            <div className="text-[13px] text-white/70">Operations & intelligence platform by Motion Imprints</div>
          </div>
        </div>
        <div className="relative mt-auto max-w-md">
          <h2 className="text-[30px] font-semibold leading-tight tracking-tight">Every branch, every till, every shilling — accounted for.</h2>
          <ul className="mt-8 space-y-4 text-[14.5px] text-white/85">
            <li className="flex gap-3"><Store className="mt-0.5 size-5 shrink-0 text-emerald-300" />Fast checkout with stock and ledger updated in the same transaction.</li>
            <li className="flex gap-3"><ShieldCheck className="mt-0.5 size-5 shrink-0 text-emerald-300" />Maker-checker approvals and a tamper-resistant audit trail.</li>
            <li className="flex gap-3"><WifiOff className="mt-0.5 size-5 shrink-0 text-emerald-300" />Branch tills keep selling through internet outages and sync later.</li>
          </ul>
        </div>
      </section>
      <section className="nature-backdrop relative isolate flex items-center justify-center overflow-hidden px-6 py-12">
        <NatureLeaves />
        <div className="w-full max-w-[380px]">
          <div className="mb-8 lg:hidden">
            <img src="/favicon.svg" alt="" className="size-10" />
            <div className="mt-3 text-lg font-semibold">{meta?.brand ?? 'Motion Retail'}</div>
          </div>
          <div className="flex items-center justify-between">
            <h1 className="text-[22px] font-semibold tracking-tight">Sign in</h1>
            {meta?.test_data && <TestDataBadge />}
          </div>
          <p className="mt-1 text-sm text-muted">{meta?.edge
            ? <>Branch till{meta.branch ? ` · ${meta.branch.name}` : ''}. Works with or without internet.</>
            : 'Use the account your manager created for you.'}</p>
          <SignedOutNotice />
          <form onSubmit={onSubmit} className="mt-7 space-y-4" noValidate>
            <Field label="Username" htmlFor="username" error={formState.errors.username?.message}>
              <Input id="username" autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus {...register('username')} />
            </Field>
            <Field label="Password" htmlFor="password" error={formState.errors.password?.message}>
              <Input id="password" type="password" autoComplete="current-password" {...register('password')} />
            </Field>
            <ErrorBox error={error} />
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={formState.isSubmitting} icon={<Lock className="size-4" />}>
              Sign in
            </Button>
          </form>
          <p className="mt-8 text-[12.5px] leading-relaxed text-muted">
            Forgot your password? Ask the Head Manager to issue a temporary one — for security, resets are done in person, not by email.
            Repeated failed attempts temporarily lock the account.
          </p>
        </div>
      </section>
    </div>
  )
}

interface DemoAccount { id: number; username: string; full_name: string; roles: string[]; branches: string[]; all_branches: boolean }

function DemoPicker({ brand }: { brand: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const q = useQuery({ queryKey: ['demo-accounts'], queryFn: () => api.get<{ accounts: DemoAccount[] }>('/api/auth/demo-accounts') })
  const go = async (username: string) => {
    setBusy(username); setError(null)
    try {
      await api.post('/api/auth/demo-login', { username })
      await qc.invalidateQueries({ queryKey: ['me'] })
      navigate('/', { replace: true })
    } catch (e) { setError(e) } finally { setBusy(null) }
  }
  const order = ['owner', 'general_manager', 'accountant', 'operations_manager', 'inventory_manager', 'branch_manager', 'cashier', 'sysadmin']
  const accounts = [...(q.data?.accounts ?? [])].sort((x, y) => order.indexOf(primaryRole(x.roles)) - order.indexOf(primaryRole(y.roles)))
  return (
    <div className="nature-backdrop relative isolate min-h-full overflow-hidden px-4 py-10 sm:px-6">
      <NatureLeaves />
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-10" />
          <div><h1 className="text-[22px] font-semibold tracking-tight">{brand}</h1><p className="text-sm text-muted">Open demo — choose an account to explore. No password needed.</p></div>
          <span className="ml-auto"><TestDataBadge /></span>
        </div>
        <div className="mt-5 rounded-xl border border-warn/30 bg-warn-soft px-4 py-3 text-[13px] text-warn">
          This is a public demonstration with synthetic TEST DATA. Each account keeps its real permissions and branch limits, and every action is recorded in the audit trail.
        </div>
        <SignedOutNotice />
        <ErrorBox error={error ?? q.error} className="mt-4" />
        {GROUPS.map((g) => {
          const members = accounts.filter((a) => g.roles.includes(primaryRole(a.roles)))
          if (!members.length) return null
          return (
            <section key={g.title} className="mt-7" aria-labelledby={`grp-${g.title}`}>
              <h2 id={`grp-${g.title}`} className="flex items-center gap-2.5 text-[15px] font-semibold tracking-tight">
                <RoleIcon role={g.icon} size="sm" />
                <span>{g.title} <span className="ml-1 text-[12.5px] font-normal text-muted">{g.hint}</span></span>
              </h2>
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {members.map((a) => (
                  <button key={a.username} onClick={() => go(a.username)} disabled={!!busy}
                    className="group flex flex-col rounded-[var(--radius-card)] border border-line-strong bg-surface p-4 text-left shadow-[var(--shadow-card)] transition hover:border-accent/60 hover:shadow-md disabled:opacity-60">
                    <span className="flex items-center gap-3">
                      <RoleIcon role={primaryRole(a.roles)} size="lg" />
                      <span className="min-w-0">
                        <span className="block text-[12px] font-semibold uppercase tracking-wide text-accent">{ROLE_LABELS[primaryRole(a.roles)]}</span>
                        <span className="mt-0.5 block truncate text-[15px] font-semibold">{a.full_name.replace(/\s*\(TEST.*\)/, '')}</span>
                      </span>
                    </span>
                    <span className="mt-0.5 text-[12.5px] text-muted">{a.username} · {a.all_branches ? 'all branches' : a.branches.join(', ') || 'no branch'}</span>
                    <span className="mt-3 text-[13px] font-medium text-accent group-hover:underline">{busy === a.username ? 'Signing in…' : 'Continue as this user →'}</span>
                  </button>
                ))}
              </div>
            </section>
          )
        })}
        {q.isLoading && <p className="mt-6 text-sm text-muted">Loading accounts…</p>}
      </div>
    </div>
  )
}

// Picker sections, in the order staff usually look for them.
const GROUPS: { title: string; hint: string; icon: string; roles: string[] }[] = [
  { title: 'Managers', hint: 'Head Manager, General, Operations and Branch Managers', icon: 'general_manager', roles: ['owner', 'general_manager', 'operations_manager', 'branch_manager'] },
  { title: 'Accountants', hint: 'Finance, payables and cash reconciliation', icon: 'accountant', roles: ['accountant'] },
  { title: 'Inventory', hint: 'Stock, products, receiving and transfers', icon: 'inventory_manager', roles: ['inventory_manager'] },
  { title: 'Cashiers', hint: 'Point of sale tills', icon: 'cashier', roles: ['cashier'] },
  { title: 'Technical', hint: 'System administration only', icon: 'sysadmin', roles: ['sysadmin'] },
]

function SignedOutNotice() {
  const [params] = useSearchParams()
  const reason = params.get('reason')
  if (reason !== 'idle') return null
  return (
    <div role="status" className="mt-4 rounded-lg border border-info/25 bg-info-soft px-3 py-2.5 text-[13px] text-info">
      You were signed out after 5 minutes without activity. Sign in again to continue.
    </div>
  )
}
