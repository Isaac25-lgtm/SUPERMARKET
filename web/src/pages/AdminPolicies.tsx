import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { ROLE_LABELS, useAuth } from '../auth/auth'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, Notice, PageHeader, Select, Table, Td, Th, useToast } from '../components/ui'
import { dateTime, humanize, money } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

const APPROVER_ROLES: Record<string, string[]> = {
  purchase_request: ['operations_manager', 'accountant', 'general_manager', 'owner'],
  expense: ['accountant', 'general_manager', 'owner', 'branch_manager'],
  supplier_payment: ['accountant', 'general_manager', 'owner'],
  transfer: ['operations_manager', 'general_manager', 'owner'],
  refund: ['branch_manager', 'general_manager', 'owner'],
  stock_writeoff: ['branch_manager', 'operations_manager', 'accountant', 'general_manager', 'owner'],
  price_override: ['branch_manager', 'general_manager', 'owner'],
}

export default function AdminPoliciesPage() {
  const { can } = useAuth()
  const q = useQuery({ queryKey: ['policies'], queryFn: () => api.get<{ policies: R[] }>('/api/admin/policies') })
  const del = useQuery({ queryKey: ['delegations'], queryFn: () => api.get<{ delegations: R[] }>('/api/admin/delegations'), enabled: can('admin.users') })
  const [edit, setEdit] = useState<R | null>(null)
  const grouped = (q.data?.policies ?? []).reduce<Record<string, R[]>>((acc, p) => { (acc[p.action_type] ??= []).push(p); return acc }, {})
  return (
    <div>
      <PageHeader title="Approval policies" subtitle="Amount bands decide which roles must approve, in order. No matching band means the action is denied." />
      <div className="mb-5"><Notice tone="warn">Thresholds shown are EXAMPLE values seeded for testing (e.g. UGX 200,000 Operations / 1,000,000 GM). Replace them with limits approved by the business owner. Changes apply to new requests; in-flight requests keep the chain they were submitted with.</Notice></div>
      {q.isLoading ? <LoadingBlock rows={8} /> : q.error ? <ErrorBox error={q.error} /> : (
        <div className="space-y-5">
          {Object.entries(grouped).map(([action, rows]) => (
            <Card key={action}>
              <CardHeader title={humanize(action)} />
              <Table><thead><tr><Th>Amount band</Th><Th>Approval chain</Th><Th>Last change</Th><Th /></tr></thead>
                <tbody>{rows.map((p) => (
                  <tr key={p.id}>
                    <Td className="whitespace-nowrap">{money(p.min_amount)} – {p.max_amount === null ? 'no limit' : money(p.max_amount)}</Td>
                    <Td><div className="flex flex-wrap items-center gap-1">{p.steps.map((s: R, i: number) => <span key={i} className="flex items-center gap-1">{i > 0 && <span className="text-muted">→</span>}<Badge tone={s.kind === 'approve' ? 'accent' : 'neutral'}>{s.label}</Badge></span>)}</div></Td>
                    <Td className="text-[12.5px] text-muted">{dateTime(p.updated_at)}<div className="max-w-[260px] truncate" title={p.note}>{p.note}</div></Td>
                    <Td align="right">{can('admin.policies') && <Button size="sm" onClick={() => setEdit(p)}>Edit</Button>}</Td>
                  </tr>))}</tbody></Table>
            </Card>
          ))}
        </div>
      )}
      {del.data && (
        <Card className="mt-5">
          <CardHeader title="Delegations of authority" subtitle="Time-limited (max 31 days), audited; the delegate inherits only the named role's approval steps." />
          {del.data.delegations.length ? <Table><thead><tr><Th>From</Th><Th>To</Th><Th>Role</Th><Th>Ceiling</Th><Th>Valid</Th><Th>Status</Th></tr></thead>
            <tbody>{del.data.delegations.map((d) => <tr key={d.id}><Td>{d.from_name}</Td><Td>{d.to_name}</Td><Td>{ROLE_LABELS[d.role_code]}</Td><Td>{d.max_amount ? money(d.max_amount) : 'Role limit'}</Td><Td className="text-[12.5px]">{dateTime(d.starts_at)} – {dateTime(d.ends_at)}</Td><Td>{d.revoked_at ? 'Revoked' : new Date(d.ends_at) < new Date() ? 'Expired' : 'Active'}</Td></tr>)}</tbody></Table> : <Empty title="No delegations" />}
        </Card>
      )}
      {edit && <EditPolicy p={edit} onClose={() => setEdit(null)} />}
    </div>
  )
}

function EditPolicy({ p, onClose }: { p: R; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [min, setMin] = useState(String(Number(p.min_amount)))
  const [max, setMax] = useState(p.max_amount === null ? '' : String(Number(p.max_amount)))
  const [steps, setSteps] = useState<R[]>(p.steps)
  const [reason, setReason] = useState('')
  const m = useMutation({
    mutationFn: () => api.put(`/api/admin/policies/${p.id}`, { min_amount: min, max_amount: max === '' ? null : max, steps, reason }),
    onSuccess: () => { toast('good', 'Policy updated and audited.'); qc.invalidateQueries({ queryKey: ['policies'] }); onClose() },
  })
  const roles = APPROVER_ROLES[p.action_type] ?? []
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title={`Edit ${humanize(p.action_type)} band`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={reason.length < 5 || !steps.length} onClick={() => m.mutate()}>Save policy</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="From amount (UGX)" htmlFor="pm"><Input id="pm" inputMode="numeric" value={min} onChange={(e) => setMin(e.target.value.replace(/[^\d]/g, ''))} /></Field>
        <Field label="Up to amount (blank = no limit)" htmlFor="px"><Input id="px" inputMode="numeric" value={max} onChange={(e) => setMax(e.target.value.replace(/[^\d]/g, ''))} /></Field>
      </div>
      <div className="mt-4 space-y-2">
        <div className="text-[13px] font-medium text-ink-2">Steps (in order)</div>
        {steps.map((s, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="w-6 text-center text-[13px] text-muted">{i + 1}</span>
            <Select aria-label="Role" value={s.role} onChange={(e) => setSteps((x) => x.map((y, j) => j === i ? { ...y, role: e.target.value } : y))}>{roles.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}</Select>
            <Input aria-label="Label" value={s.label} onChange={(e) => setSteps((x) => x.map((y, j) => j === i ? { ...y, label: e.target.value } : y))} />
            <Select aria-label="Kind" className="w-32" value={s.kind} onChange={(e) => setSteps((x) => x.map((y, j) => j === i ? { ...y, kind: e.target.value } : y))}><option value="review">review</option><option value="verify">verify</option><option value="approve">approve</option></Select>
            <Button variant="ghost" aria-label="Remove step" onClick={() => setSteps((x) => x.filter((_, j) => j !== i))}><Trash2 className="size-4" /></Button>
          </div>
        ))}
        <Button size="sm" icon={<Plus className="size-4" />} onClick={() => setSteps((x) => [...x, { role: roles[0], label: 'Approval', kind: 'approve' }])}>Add step</Button>
      </div>
      <Field label="Reason for the change (audited)" htmlFor="pr" className="mt-4"><Input id="pr" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
