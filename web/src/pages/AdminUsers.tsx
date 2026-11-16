import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, UserPlus, Users } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { ROLE_LABELS } from '../auth/auth'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, Notice, PageHeader, StatusChip, Table, Td, Th } from '../components/ui'
import { relative } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function AdminUsersPage() {
  const q = useQuery({ queryKey: ['admin-users'], queryFn: () => api.get<{ users: R[]; roles: R[]; branches: R[] }>('/api/admin/users') })
  const alerts = useQuery({ queryKey: ['security-alerts'], queryFn: () => api.get<{ alerts: R[] }>('/api/admin/security-alerts') })
  const [creating, setCreating] = useState(false)
  const codes = Object.fromEntries((q.data?.branches ?? []).map((b) => [b.id, b.code]))
  return (
    <div>
      <PageHeader title="Users & access" subtitle="Create accounts, grant roles and branches, suspend access. Every change is audited and takes effect immediately."
        actions={<Button variant="primary" icon={<UserPlus className="size-4" />} onClick={() => setCreating(true)}>Add user</Button>} />
      {alerts.data?.alerts.length ? (
        <Card className="mb-5"><CardHeader title="Security alerts" icon={<ShieldAlert className="size-4" />} />
          <ul className="divide-y divide-line">{alerts.data.alerts.map((a, i) => <li key={i} className="flex items-center justify-between px-5 py-2.5 text-[13.5px]"><span className="flex items-center gap-2"><StatusChip status={a.severity} />{a.title}</span><span className="text-[12px] text-muted">{relative(a.at)}</span></li>)}</ul>
        </Card>
      ) : null}
      <Card>
        {q.isLoading ? <LoadingBlock rows={8} /> : q.error ? <div className="p-4"><ErrorBox error={q.error} /></div> : q.data?.users.length ? (
          <Table><thead><tr><Th>User</Th><Th>Roles</Th><Th>Branches</Th><Th>Status</Th><Th>Last sign-in</Th><Th align="right">Sessions</Th><Th align="right">Failed (7d)</Th></tr></thead>
            <tbody>{q.data.users.map((u) => (
              <tr key={u.id} className="hover:bg-surface-2/50">
                <Td><Link to={`/admin/users/${u.id}`} className="font-medium hover:text-accent hover:underline">{u.full_name}</Link><div className="text-[12px] text-muted">{u.username}{u.must_change_password ? ' · must change password' : ''}</div></Td>
                <Td><div className="flex flex-wrap gap-1">{u.roles.map((r: string) => <Badge key={r}>{ROLE_LABELS[r]}</Badge>)}</div></Td>
                <Td className="text-[12.5px]">{u.all_branches ? 'All branches' : u.branch_ids.map((b: number) => codes[b]).join(', ') || '—'}</Td>
                <Td><StatusChip status={u.locked_until && new Date(u.locked_until) > new Date() ? 'denied' : u.status} label={u.locked_until && new Date(u.locked_until) > new Date() ? 'Locked' : undefined} /></Td>
                <Td>{relative(u.last_login_at)}</Td><Td align="right">{u.active_sessions}</Td><Td align="right" className={u.failures_7d >= 3 ? 'font-semibold text-bad' : ''}>{u.failures_7d}</Td>
              </tr>))}</tbody></Table>
        ) : <Empty title="No users" icon={<Users className="size-6" />} />}
      </Card>
      {creating && q.data && <CreateUser roles={q.data.roles} branches={q.data.branches} onClose={() => setCreating(false)} />}
    </div>
  )
}

export function AccessEditor({ roles, branches, value, onChange }: { roles: R[]; branches: R[]; value: { roles: string[]; branch_ids: number[]; all_branches: boolean }; onChange: (v: { roles: string[]; branch_ids: number[]; all_branches: boolean }) => void }) {
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="mb-2 text-[13px] font-medium text-ink-2">Roles</legend>
        <div className="grid gap-2 sm:grid-cols-2">{roles.map((r) => (
          <label key={r.code} className="flex cursor-pointer gap-2.5 rounded-lg border border-line p-2.5 has-[:checked]:border-accent has-[:checked]:bg-accent-soft/50">
            <input type="checkbox" className="mt-0.5 size-4" checked={value.roles.includes(r.code)} onChange={() => onChange({ ...value, roles: toggle(value.roles, r.code) })} />
            <span><span className="block text-[13.5px] font-medium">{r.name}</span><span className="block text-[12px] text-muted">{r.description}</span></span>
          </label>))}</div>
      </fieldset>
      <fieldset>
        <legend className="mb-2 text-[13px] font-medium text-ink-2">Branch access</legend>
        <label className="mb-2 flex items-center gap-2 text-[13.5px]"><input type="checkbox" className="size-4" checked={value.all_branches} onChange={(e) => onChange({ ...value, all_branches: e.target.checked })} />All branches (head-office staff)</label>
        {!value.all_branches && <div className="flex flex-wrap gap-2">{branches.map((b) => <label key={b.id} className="flex items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-[13px] has-[:checked]:border-accent has-[:checked]:bg-accent-soft/50"><input type="checkbox" className="size-4" checked={value.branch_ids.includes(b.id)} onChange={() => onChange({ ...value, branch_ids: toggle(value.branch_ids, b.id) })} />{b.name}</label>)}</div>}
      </fieldset>
    </div>
  )
}

function CreateUser({ roles, branches, onClose }: { roles: R[]; branches: R[]; onClose: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ username: '', full_name: '', email: '' })
  const [access, setAccess] = useState({ roles: ['cashier'], branch_ids: [] as number[], all_branches: false })
  const [created, setCreated] = useState<R | null>(null)
  const m = useMutation({ mutationFn: () => api.post<R>('/api/admin/users', { ...f, email: f.email || null, ...access }), onSuccess: (r) => { setCreated(r); qc.invalidateQueries({ queryKey: ['admin-users'] }) } })
  if (created) {
    return (
      <Modal open onOpenChange={(v) => !v && onClose()} title="User created" footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
        <Notice tone="warn">Give this temporary password to <b>{created.username}</b> in person. It is shown only once and must be changed at first sign-in.</Notice>
        <div className="mt-4 rounded-xl border border-line-strong bg-surface-2 p-4 text-center font-mono text-xl tracking-wider" data-testid="temp-password">{created.temporary_password}</div>
      </Modal>
    )
  }
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title="Add a staff account" description="Grant only the roles and branches the person needs."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!f.username || !f.full_name || !access.roles.length} onClick={() => m.mutate()}>Create account</Button></>}>
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Field label="Full name" htmlFor="un"><Input id="un" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Username" htmlFor="uu" hint="lowercase, e.g. cash.north3"><Input id="uu" autoCapitalize="none" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.toLowerCase() })} /></Field>
        <Field label="Email (optional)" htmlFor="ue"><Input id="ue" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
      </div>
      <AccessEditor roles={roles} branches={branches} value={access} onChange={setAccess} />
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
