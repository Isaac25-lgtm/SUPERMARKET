import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, LogOut, ShieldOff, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, CardHeader, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, Notice, PageHeader, StatusChip, Table, Td, Textarea, Th, useToast } from '../components/ui'
import { dateTime, humanize, money } from '../lib/format'
import { AccessEditor } from './AdminUsers'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function AdminUserDetailPage() {
  const { id } = useParams()
  const { me } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const u = useQuery({ queryKey: ['admin-user', id], queryFn: () => api.get<R>(`/api/admin/users/${id}`) })
  const meta = useQuery({ queryKey: ['admin-users'], queryFn: () => api.get<{ users: R[]; roles: R[]; branches: R[] }>('/api/admin/users') })
  const [access, setAccess] = useState<{ roles: string[]; branch_ids: number[]; all_branches: boolean } | null>(null)
  const [reason, setReason] = useState('')
  const [action, setAction] = useState<'suspend' | 'reactivate' | 'revoke-sessions' | 'reset-password' | null>(null)
  const [tempPw, setTempPw] = useState<string | null>(null)
  useEffect(() => { if (u.data && !access) setAccess({ roles: u.data.roles, branch_ids: u.data.branch_ids, all_branches: u.data.all_branches }) }, [u.data, access])
  const refresh = () => { qc.invalidateQueries({ queryKey: ['admin-user', id] }); qc.invalidateQueries({ queryKey: ['admin-users'] }) }
  const save = useMutation({ mutationFn: () => api.put(`/api/admin/users/${id}/access`, { ...access, reason }), onSuccess: () => { toast('good', 'Access updated. Existing sessions were signed out.'); setReason(''); refresh() } })
  if (u.isLoading || !access) return <LoadingBlock rows={8} />
  if (u.error) return <ErrorBox error={u.error} />
  const user = u.data!
  const isSelf = me?.id === user.id
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title={user.full_name} subtitle={<>{user.username} · created {dateTime(user.created_at)} · last sign-in {dateTime(user.last_login_at)}</>}
        actions={<><StatusChip status={user.status} />{!isSelf && <>
          {user.status === 'active' ? <Button variant="danger" icon={<ShieldOff className="size-4" />} onClick={() => setAction('suspend')}>Suspend</Button> : <Button icon={<ShieldCheck className="size-4" />} onClick={() => setAction('reactivate')}>Reactivate</Button>}
          <Button icon={<LogOut className="size-4" />} onClick={() => setAction('revoke-sessions')}>Sign out everywhere</Button>
          <Button icon={<KeyRound className="size-4" />} onClick={() => setAction('reset-password')}>Reset password</Button></>}</>} />
      {isSelf && <div className="mb-4"><Notice tone="warn">You cannot change your own roles, branches or status — another Head Manager must do it. This prevents self-escalation.</Notice></div>}
      <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader title="Roles & branch access" subtitle={`${user.active_sessions} active session(s) · ${user.failures_7d} failed sign-ins in 7 days`} />
          <div className="p-5">
            <fieldset disabled={isSelf}><AccessEditor roles={meta.data?.roles ?? []} branches={meta.data?.branches ?? []} value={access} onChange={setAccess} /></fieldset>
            {!isSelf && <div className="mt-4 space-y-3"><Field label="Reason for change (recorded in the audit trail)" htmlFor="ar"><Input id="ar" value={reason} onChange={(e) => setReason(e.target.value)} /></Field><ErrorBox error={save.error} /><Button variant="primary" loading={save.isPending} disabled={reason.length < 3} onClick={() => save.mutate()}>Save access</Button></div>}
          </div>
        </Card>
        <Card>
          <CardHeader title="Permission & status history" />
          {user.history.length ? <ol className="divide-y divide-line">{user.history.map((h: R, i: number) => (
            <li key={i} className="px-5 py-3 text-[13px]"><div className="flex justify-between gap-2"><span className="font-medium">{humanize(h.action)}</span><span className="text-[12px] text-muted">{dateTime(h.at)}</span></div><div className="text-muted">by {h.actor_username ?? 'system'}{h.reason ? ` — ${h.reason}` : ''}</div>
              {h.after_state?.roles && <div className="mt-1 text-[12px]">Roles: {(h.before_state?.roles ?? []).join(', ') || '—'} → <b>{h.after_state.roles.join(', ')}</b></div>}</li>))}</ol> : <Empty title="No changes recorded" />}
        </Card>
      </div>
      <Card className="mt-5">
        <CardHeader title="Recent activity by this user" subtitle={<Link to={`/audit?actor=${user.id}`} className="text-accent hover:underline">Open in audit trail</Link>} />
        {user.recent_activity.length ? <Table><thead><tr><Th>When</Th><Th>Action</Th><Th>Outcome</Th><Th>Target</Th><Th align="right">Amount</Th></tr></thead>
          <tbody>{user.recent_activity.map((a: R, i: number) => <tr key={i}><Td>{dateTime(a.at)}</Td><Td>{a.action}</Td><Td><StatusChip status={a.outcome} /></Td><Td className="text-[12.5px] text-muted">{a.target_type} {a.target_id}</Td><Td align="right">{a.amount ? money(a.amount) : ''}</Td></tr>)}</tbody></Table> : <Empty title="No activity yet" />}
      </Card>
      {action && <ActionModal userId={user.id} action={action} name={user.full_name} onClose={() => setAction(null)} onDone={(pw) => { setAction(null); if (pw) setTempPw(pw); toast('good', 'Done.'); refresh() }} />}
      {tempPw && <Modal open onOpenChange={(v) => !v && setTempPw(null)} title="Temporary password" footer={<Button variant="primary" onClick={() => setTempPw(null)}>Done</Button>}>
        <Notice tone="warn">Shown once. Give it to the user in person; they must change it at next sign-in. All their sessions were ended.</Notice>
        <div className="mt-4 rounded-xl border border-line bg-surface-2 p-4 text-center font-mono text-xl tracking-wider">{tempPw}</div>
      </Modal>}
    </div>
  )
}

function ActionModal({ userId, action, name, onClose, onDone }: { userId: number; action: string; name: string; onClose: () => void; onDone: (pw?: string) => void }) {
  const [reason, setReason] = useState('')
  const m = useMutation({ mutationFn: () => api.post<R>(`/api/admin/users/${userId}/${action}`, { reason }), onSuccess: (r) => onDone(r?.temporary_password) })
  const titles: Record<string, string> = { suspend: `Suspend ${name}`, reactivate: `Reactivate ${name}`, 'revoke-sessions': `Sign ${name} out everywhere`, 'reset-password': `Reset password for ${name}` }
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={titles[action]} description={action === 'suspend' ? 'All active sessions end immediately and the account cannot sign in.' : undefined}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant={action === 'suspend' ? 'danger' : 'primary'} loading={m.isPending} disabled={reason.length < 3} onClick={() => m.mutate()}>Confirm</Button></>}>
      <Field label="Reason (recorded in the audit trail)" htmlFor="rs"><Textarea id="rs" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus /></Field>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
