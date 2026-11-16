import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Building2, Check, ChevronDown, ClipboardCheck, Minus, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { Badge, Button, ErrorBox, LoadingBlock, Modal } from './ui'

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
  id: string; title: string; count?: string; open: boolean; onToggle: () => void; children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-line">
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

export function MyAccessDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const q = useQuery({ queryKey: ['my-access'], queryFn: () => api.get<Access>('/api/auth/access'), enabled: open, staleTime: 60_000 })
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
    <Modal open={open} onOpenChange={onOpenChange} wide title="My access"
      description="What this account can see and do. The server checks these rights again on every action.">
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
    </Modal>
  )
}
