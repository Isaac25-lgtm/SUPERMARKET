import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Building2, Check, ChevronDown, ClipboardCheck, Minus, ShieldCheck, Users } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { Badge, Button, ErrorBox, LoadingBlock, Modal, Tabs } from './ui'

interface Perm { code: string; label: string; granted: boolean }
interface Area { area: string; granted: number; total: number; permissions: Perm[] }
interface Access {
  user: { username: string; full_name: string }
  roles: { code: string; name: string; description: string }[]
  all_branches: boolean
  branches: { id: number; code: string; name: string }[]
  areas: Area[]
  granted: number
  total: number
  can_approve: string[]
}

function Section({ id, title, count, open, onToggle, children }: {
  id: string; title: React.ReactNode; count?: string; open: boolean; onToggle: () => void; children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-line-strong">
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={id}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13.5px] font-semibold hover:bg-surface-2">
        <span className="flex-1">{title}</span>
        {count && <span className="text-[12px] font-normal text-muted">{count}</span>}
        <ChevronDown className={clsx('size-4 text-muted transition-transform', open && 'rotate-180')} />
      </button>
      {open && <div id={id} className="border-t border-line px-3.5 py-2.5">{children}</div>}
    </div>
  )
}

function MyAccessView() {
  const q = useQuery({ queryKey: ['my-access'], queryFn: () => api.get<Access>('/api/auth/access'), staleTime: 60_000 })
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const d = q.data
  const granted = d?.areas.filter((a) => a.granted > 0) ?? []
  const missing = d?.areas.flatMap((a) => a.permissions.filter((p) => !p.granted)) ?? []
  const keys = ['roles', 'branches', 'approvals', ...granted.map((a) => a.area)]
  const isOpen = (k: string, dflt = true) => !(collapsed[k] ?? !dflt)
  const toggle = (k: string, dflt = true) => setCollapsed((c) => ({ ...c, [k]: isOpen(k, dflt) }))
  const allCollapsed = keys.every((k) => !isOpen(k))
  const setAll = (close: boolean) => setCollapsed(Object.fromEntries([...keys.map((k) => [k, close]), ['missing', true]]))

  return (
    <>
      {q.isLoading ? <LoadingBlock rows={5} /> : q.error ? <ErrorBox error={q.error} /> : d && (
        <div className="space-y-3" data-testid="my-access">
          <div className="flex flex-wrap items-center gap-3">
            <span className="grid size-10 place-items-center rounded-full bg-accent-soft text-[13px] font-semibold text-accent-ink">
              {d.user.full_name.split(' ').slice(0, 2).map((s) => s[0]).join('')}
            </span>
            <div className="min-w-0 flex-1 leading-tight">
              <div className="truncate text-[15px] font-semibold">{d.user.full_name}</div>
              <div className="text-[12.5px] text-muted">{d.user.username} · {d.granted} of {d.total} permissions</div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setAll(!allCollapsed)}>{allCollapsed ? 'Expand all' : 'Collapse all'}</Button>
          </div>

          <Section id="acc-roles" title={d.roles.length === 1 ? 'Role' : 'Roles'} count={String(d.roles.length)} open={isOpen('roles')} onToggle={() => toggle('roles')}>
            <ul className="space-y-2">
              {d.roles.map((r) => (
                <li key={r.code} className="flex gap-2.5">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />
                  <div><div className="text-[13.5px] font-medium">{r.name}</div><div className="text-[12.5px] text-muted">{r.description}</div></div>
                </li>
              ))}
            </ul>
          </Section>

          <Section id="acc-branches" title="Branches" count={d.all_branches ? 'All' : String(d.branches.length)} open={isOpen('branches')} onToggle={() => toggle('branches')}>
            <div className="flex flex-wrap gap-1.5">
              {d.all_branches && <Badge tone="info"><Building2 className="size-3.5" />All branches</Badge>}
              {!d.all_branches && d.branches.map((b) => <Badge key={b.id}><Building2 className="size-3.5" />{b.name}</Badge>)}
              {!d.all_branches && !d.branches.length && <span className="text-[13px] text-muted">No branch assigned.</span>}
            </div>
          </Section>

          <Section id="acc-approvals" title="Approvals your role can sign" count={String(d.can_approve.length)} open={isOpen('approvals')} onToggle={() => toggle('approvals')}>
            {d.can_approve.length ? (
              <>
                <div className="flex flex-wrap gap-1.5">{d.can_approve.map((a) => <Badge key={a}><ClipboardCheck className="size-3.5" />{a}</Badge>)}</div>
                <p className="mt-2 text-[12px] text-muted">Subject to the approval policy, amount limits and your branches. You can never approve your own request.</p>
              </>
            ) : <span className="text-[13px] text-muted">This account does not approve requests.</span>}
          </Section>

          <div className="pt-1 text-[12px] font-semibold uppercase tracking-wide text-muted">Permissions by area</div>
          {granted.map((a) => (
            <Section key={a.area} id={`acc-${a.area}`} title={a.area} count={`${a.granted} of ${a.total}`} open={isOpen(a.area)} onToggle={() => toggle(a.area)}>
              <ul className="space-y-1.5">
                {a.permissions.filter((p) => p.granted).map((p) => (
                  <li key={p.code} className="flex gap-2 text-[13px]"><Check className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />{p.label}</li>
                ))}
              </ul>
            </Section>
          ))}
          {missing.length > 0 && (
            <Section id="acc-missing" title="Not included in your roles" count={String(missing.length)} open={isOpen('missing', false)} onToggle={() => toggle('missing', false)}>
              <ul className="space-y-1.5">
                {missing.map((p) => (
                  <li key={p.code} className="flex gap-2 text-[13px] text-muted"><Minus className="mt-0.5 size-4 shrink-0" aria-hidden />{p.label}</li>
                ))}
              </ul>
              <p className="mt-2 text-[12px] text-muted">Need more access? Ask the Head Manager, who manages users and roles.</p>
            </Section>
          )}
        </div>
      )}
    </>
  )
}

interface RoleCard {
  code: string; name: string; description: string; head_office: boolean; granted: number
  areas: { area: string; permissions: { code: string; label: string }[] }[]
  can_approve: string[]
  people?: { username: string; full_name: string }[]
}
interface Directory {
  roles: RoleCard[]
  matrix: { area: string; permissions: { code: string; label: string; roles: string[] }[] }[]
  approvals: { action: string; roles: string[] }[]
  total: number
  my_roles: string[]
  shows_people: boolean
}

// Short column headings for the comparison table (full names are in the cards and tooltips).
const SHORT: Record<string, string> = {
  owner: 'Head Mgr', general_manager: 'GM', accountant: 'Accounts', operations_manager: 'Ops',
  inventory_manager: 'Inventory', branch_manager: 'Branch Mgr', cashier: 'Cashier', sysadmin: 'Sys admin',
}

const subhead = 'mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-muted'

function AllRolesView() {
  const q = useQuery({ queryKey: ['roles-directory'], queryFn: () => api.get<Directory>('/api/auth/roles'), staleTime: 300_000 })
  const [view, setView] = useState<'roles' | 'compare'>('roles')
  const [openRoles, setOpenRoles] = useState<Record<string, boolean>>({})
  const d = q.data
  if (q.isLoading) return <LoadingBlock rows={6} />
  if (q.error) return <ErrorBox error={q.error} />
  if (!d) return null
  const mine = new Set(d.my_roles)
  const isOpen = (c: string) => openRoles[c] ?? mine.has(c)
  const allOpen = d.roles.every((r) => isOpen(r.code))
  const mark = (on: boolean) => on
    ? <Check className="mx-auto size-4 text-good" aria-label="Yes" />
    : <Minus className="mx-auto size-3.5 text-line-strong" aria-label="No" />
  const cell = (code: string) => clsx('border-b border-l border-line px-2 py-1.5', mine.has(code) && 'bg-accent-soft/50')

  return (
    <div className="space-y-3" data-testid="all-roles">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs value={view} onChange={setView} items={[{ value: 'roles', label: 'By role', count: d.roles.length }, { value: 'compare', label: 'Compare roles' }]} />
        <span className="flex-1" />
        {view === 'roles' && (
          <Button size="sm" variant="ghost" onClick={() => setOpenRoles(Object.fromEntries(d.roles.map((r) => [r.code, !allOpen])))}>
            {allOpen ? 'Collapse all' : 'Expand all'}
          </Button>
        )}
      </div>

      {view === 'roles' && d.roles.map((r) => (
        <Section key={r.code} id={`role-${r.code}`} open={isOpen(r.code)}
          onToggle={() => setOpenRoles((o) => ({ ...o, [r.code]: !isOpen(r.code) }))}
          count={`${r.granted} of ${d.total}`}
          title={<span className="flex flex-wrap items-center gap-2">{r.name}{mine.has(r.code) && <Badge tone="info">Your role</Badge>}</span>}>
          <div className="space-y-3">
            <p className="text-[13px] text-ink-2">{r.description}</p>
            <div className="flex flex-wrap gap-1.5">
              <Badge><Building2 className="size-3.5" />{r.head_office ? 'Head office role' : 'Branch role'}</Badge>
            </div>
            <div>
              <div className={subhead}>Approvals this role can sign</div>
              {r.can_approve.length
                ? <div className="flex flex-wrap gap-1.5">{r.can_approve.map((a) => <Badge key={a}><ClipboardCheck className="size-3.5" />{a}</Badge>)}</div>
                : <span className="text-[13px] text-muted">Does not approve requests.</span>}
            </div>
            {r.areas.map((a) => (
              <div key={a.area}>
                <div className={subhead}>{a.area}</div>
                <ul className="space-y-1.5">
                  {a.permissions.map((p) => (
                    <li key={p.code} className="flex gap-2 text-[13px]"><Check className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />{p.label}</li>
                  ))}
                </ul>
              </div>
            ))}
            {d.shows_people && (
              <div>
                <div className={subhead}>People with this role</div>
                {r.people?.length
                  ? <div className="flex flex-wrap gap-1.5">{r.people.map((u) => <Badge key={u.username}><Users className="size-3.5" />{u.full_name}</Badge>)}</div>
                  : <span className="text-[13px] text-muted">No active account has this role.</span>}
              </div>
            )}
          </div>
        </Section>
      ))}

      {view === 'compare' && (
        <div className="space-y-3">
          <div className="overflow-x-auto rounded-xl border border-line-strong">
            <table className="w-full border-collapse text-[12.5px]">
              <thead>
                <tr>
                  <th scope="col" className="sticky left-0 z-10 min-w-[170px] border-b border-line-strong bg-surface-2 px-3 py-2 text-left text-[11.5px] font-semibold uppercase tracking-wide text-muted">Permission</th>
                  {d.roles.map((r) => (
                    <th key={r.code} scope="col" title={r.name}
                      className={clsx('whitespace-nowrap border-b border-l border-line-strong bg-surface-2 px-2 py-2 text-center text-[11.5px] font-semibold', mine.has(r.code) ? 'text-accent-ink' : 'text-muted')}>
                      {SHORT[r.code] ?? r.code}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.matrix.map((a) => [
                  <tr key={a.area}><th colSpan={d.roles.length + 1} scope="colgroup" className="border-b border-line bg-surface-2/60 px-3 py-1.5 text-left text-[12px] font-semibold">{a.area}</th></tr>,
                  ...a.permissions.map((p) => (
                    <tr key={p.code}>
                      <th scope="row" className="sticky left-0 border-b border-line bg-surface px-3 py-1.5 text-left font-normal">{p.label}</th>
                      {d.roles.map((r) => <td key={r.code} className={cell(r.code)}>{mark(p.roles.includes(r.code))}</td>)}
                    </tr>
                  )),
                ])}
                <tr><th colSpan={d.roles.length + 1} scope="colgroup" className="border-b border-line bg-surface-2/60 px-3 py-1.5 text-left text-[12px] font-semibold">Approvals each role can sign</th></tr>
                {d.approvals.map((a) => (
                  <tr key={a.action}>
                    <th scope="row" className="sticky left-0 border-b border-line bg-surface px-3 py-1.5 text-left font-normal">{a.action}</th>
                    {d.roles.map((r) => <td key={r.code} className={cell(r.code)}>{mark(a.roles.includes(r.code))}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[12px] text-muted">Your role's column is highlighted. Approval rights also depend on the approval policy, amount limits and branch. Nobody can approve their own request.</p>
        </div>
      )}
    </div>
  )
}

export type AccessTab = 'mine' | 'roles'

export function MyAccessDialog({ open, onOpenChange, tab, onTab }: {
  open: boolean; onOpenChange: (v: boolean) => void; tab: AccessTab; onTab: (t: AccessTab) => void
}) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} wide title={tab === 'mine' ? 'My access' : 'Roles & permissions'}
      description={tab === 'mine'
        ? 'What this account can see and do. The server checks these rights again on every action.'
        : 'What each role is responsible for and what it can do. This is the same rule book the server enforces.'}>
      <div className="mb-3"><Tabs value={tab} onChange={onTab} items={[{ value: 'mine', label: 'My access' }, { value: 'roles', label: 'All roles' }]} /></div>
      {open && (tab === 'mine' ? <MyAccessView /> : <AllRolesView />)}
    </Modal>
  )
}
